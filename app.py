"""BrandGuard: pre-publication brand governance. Flask app (server-rendered).

Roles: creator (submits, responds to findings), manager / reviewer (approve or request changes),
admin (manages the approved knowledge base). AI only suggests; every decision is a person's.
"""
import difflib
import json
import os
import re
import secrets
import time
from functools import wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash

import db as dbm
import engine
import seed
from db import audit, execute, now, query

EDITABLE = ("draft", "changes_requested")
REVIEW_ROLES = ("manager", "reviewer", "admin")
CHANNELS = {"social": "Social post", "press_release": "Press release", "website": "Website copy", "email": "Email"}
STATUS_LABEL = {"draft": "Draft", "in_review": "In review", "changes_requested": "Changes requested",
                "approved": "Approved", "rejected": "Rejected"}
KB_KINDS = {"guideline": "Guidelines", "fact": "Approved facts", "term": "Terminology",
            "claim": "Approved claims", "asset": "Palette (assets)"}
TOPICS = ("tone", "claims", "accessibility", "visual", "messaging", "general")


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.environ.get("DATABASE_PATH") or os.path.join(app.instance_path, "brandguard.db"),
        ANTHROPIC_API_KEY=os.environ.get("ANTHROPIC_API_KEY", ""),
        ANTHROPIC_MODEL=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        AI_USER_HOURLY_LIMIT=int(os.environ.get("AI_USER_HOURLY_LIMIT", "15")),
        AI_DAILY_LIMIT=int(os.environ.get("AI_DAILY_LIMIT", "300")),
        DEMO_MODE=os.environ.get("DEMO_MODE", "1") == "1",
        CSRF_ENABLED=True,
        MAX_CONTENT_LENGTH=512 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config:
        app.config.update(config)
    os.makedirs(os.path.dirname(app.config["DATABASE"]) or ".", exist_ok=True)
    app.teardown_appcontext(dbm.close_db)

    # ------------------------------------------------------------------ helpers
    def ai_enabled():
        return bool(app.config["ANTHROPIC_API_KEY"])

    def ai_allowed(user_id):
        t = time.time()
        mine = query("SELECT COUNT(*) c FROM ai_usage WHERE user_id=? AND ts>?", (user_id, t - 3600), one=True)["c"]
        day = query("SELECT COUNT(*) c FROM ai_usage WHERE ts>?", (t - 86400,), one=True)["c"]
        if mine >= app.config["AI_USER_HOURLY_LIMIT"]:
            return False, "paused: hourly AI limit reached (rule-based checks still ran)"
        if day >= app.config["AI_DAILY_LIMIT"]:
            return False, "paused: daily AI limit reached (rule-based checks still ran)"
        return True, ""

    def latest_review(sid, vnum):
        return query("SELECT * FROM reviews WHERE submission_id=? AND version_number=? ORDER BY id DESC LIMIT 1",
                     (sid, vnum), one=True)

    def run_review(sid, vnum, user_id, use_ai=True, carry_from=None):
        sub = query("SELECT * FROM submissions WHERE id=?", (sid,), one=True)
        ver = query("SELECT * FROM versions WHERE submission_id=? AND number=?", (sid, vnum), one=True)
        kb = engine.load_kb(query("SELECT * FROM kb_items"))
        ai, note = None, ""
        if use_ai and ai_enabled():
            ok, why = ai_allowed(user_id)
            if ok:
                ai = {"api_key": app.config["ANTHROPIC_API_KEY"], "model": app.config["ANTHROPIC_MODEL"]}
            else:
                note = why
        findings, stats, ai_status = engine.review(ver["text"], sub["channel"], sub["intent"], kb, ai)
        if ai is not None:
            execute("INSERT INTO ai_usage(ts, user_id) VALUES (?,?)", (time.time(), user_id))
        elif note:
            ai_status = note
        carry = {}
        prev = latest_review(sid, carry_from or vnum)  # keep earlier accept/dispute responses on unchanged text
        if prev:
            for r in query("SELECT * FROM findings WHERE review_id=?", (prev["id"],)):
                if r["status"] != "open":
                    carry[(r["category"], r["excerpt"], r["source_id"] or 0)] = r
        rid = execute("INSERT INTO reviews(submission_id, version_number, created_at, ai_status, stats) VALUES (?,?,?,?,?)",
                      (sid, vnum, now(), ai_status, json.dumps(stats)))
        for f in findings:
            c = carry.get((f["category"], f["excerpt"], f["source_id"] or 0))
            execute(
                "INSERT INTO findings(review_id, submission_id, version_number, category, severity, span_start, span_end,"
                " excerpt, explanation, source_kind, source_id, source_label, source_quote, confidence, suggestion,"
                " advice, detected_by, status, edited_text, dispute_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, sid, vnum, f["category"], f["severity"], f["start"], f["end"], f["excerpt"], f["explanation"],
                 f["source_kind"], f["source_id"], f["source_label"], f["source_quote"], f["confidence"],
                 f["suggestion"], f["advice"], f["detected_by"], c["status"] if c else "open",
                 c["edited_text"] if c else None, c["dispute_reason"] if c else None))
        audit(user_id, "review_run", f"v{vnum}: {len(findings)} findings; AI layer: {ai_status}", sid)
        return rid

    def create_submission(user_id, title, channel, intent, text, use_ai=True):
        t = now()
        sid = execute("INSERT INTO submissions(title, channel, intent, creator_id, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                      (title, channel, intent, user_id, t, t))
        execute("INSERT INTO versions(submission_id, number, text, note, created_by, created_at) VALUES (?,?,?,?,?,?)",
                (sid, 1, text, "Initial draft", user_id, t))
        audit(user_id, "submission_created", title, sid)
        run_review(sid, 1, user_id, use_ai)
        return sid

    def counts(sub):
        c = {"open": 0, "accepted": 0, "edited": 0, "disputed": 0, "total": 0, "high_open": 0}
        rv = latest_review(sub["id"], sub["current_version"])
        if rv:
            for f in query("SELECT severity, status FROM findings WHERE review_id=?", (rv["id"],)):
                c["total"] += 1
                c[f["status"]] += 1
                if f["severity"] == "high" and f["status"] == "open":
                    c["high_open"] += 1
        return c

    def load_sub(sid):
        sub = query("SELECT s.*, u.name AS creator_name FROM submissions s JOIN users u ON u.id=s.creator_id WHERE s.id=?",
                    (sid,), one=True)
        if not sub:
            abort(404)
        if g.user["role"] not in REVIEW_ROLES and sub["creator_id"] != g.user["id"]:
            abort(403)
        return sub

    def is_owner_editor(sub):
        return sub["creator_id"] == g.user["id"] and sub["status"] in EDITABLE

    def highlight(text, findings):
        out, pos = [], 0
        for f in findings:
            if f["span_start"] < pos:
                continue
            out.append(escape(text[pos:f["span_start"]]))
            out.append(Markup('<a class="hl sev-{} st-{}" id="hl-{}" href="#finding-{}">{}</a>').format(
                f["severity"], f["status"], f["id"], f["id"], text[f["span_start"]:f["span_end"]]))
            pos = f["span_end"]
        out.append(escape(text[pos:]))
        return Markup("").join(out)

    def diff_html(a, b):
        ta, tb = re.findall(r"\s+|\w+|[^\w\s]", a), re.findall(r"\s+|\w+|[^\w\s]", b)
        parts = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
            if tag == "equal":
                parts.append(escape("".join(ta[i1:i2])))
                continue
            if i2 > i1:
                parts.append(Markup("<del>{}</del>").format("".join(ta[i1:i2])))
            if j2 > j1:
                parts.append(Markup("<ins>{}</ins>").format("".join(tb[j1:j2])))
        return Markup("").join(parts)

    def safe_next(url):
        return url if url and url.startswith("/") and not url.startswith("//") else url_for("dashboard")

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            return fn(*a, **k)
        return wrapper

    def role_required(*roles):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **k):
                if g.user["role"] not in roles:
                    abort(403)
                return fn(*a, **k)
            return wrapper
        return deco

    # ------------------------------------------------------------------ request hooks
    @app.before_request
    def load_user():
        uid = session.get("uid")
        g.user = query("SELECT * FROM users WHERE id=?", (uid,), one=True) if uid else None
        if request.method == "POST" and app.config["CSRF_ENABLED"]:
            tok = session.get("_csrf", "")
            if not tok or not secrets.compare_digest(tok, request.form.get("_csrf", "")):
                abort(400)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.context_processor
    def inject():
        def csrf_token():
            if "_csrf" not in session:
                session["_csrf"] = secrets.token_hex(16)
            return session["_csrf"]
        return {"csrf_token": csrf_token, "channels": CHANNELS, "status_label": STATUS_LABEL,
                "ai_enabled": ai_enabled(), "REVIEW_ROLES": REVIEW_ROLES}

    for code, msg in ((400, "That form expired or was invalid. Please go back, reload the page and try again."),
                      (403, "You do not have permission to do that."), (404, "We could not find that page."),
                      (413, "That upload is too large.")):
        app.register_error_handler(code, lambda e, code=code, msg=msg: (render_template("error.html", code=code, msg=msg), code))

    # ------------------------------------------------------------------ auth
    @app.route("/")
    def index():
        return redirect(url_for("dashboard" if g.user else "login"))

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            u = query("SELECT * FROM users WHERE email=?", (email,), one=True)
            if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
                session.clear()
                session["uid"] = u["id"]
                audit(u["id"], "login", "")
                return redirect(safe_next(request.args.get("next")))
            flash("Wrong email or password.", "error")
        demo = query("SELECT name, email, role FROM users ORDER BY id") if app.config["DEMO_MODE"] else []
        return render_template("login.html", demo=demo, demo_password=seed.DEMO_PASSWORD)

    @app.post("/demo-login/<role>")
    def demo_login(role):
        if not app.config["DEMO_MODE"]:
            abort(404)
        u = query("SELECT * FROM users WHERE role=? ORDER BY id LIMIT 1", (role,), one=True)
        if not u:
            abort(404)
        session.clear()
        session["uid"] = u["id"]
        audit(u["id"], "login", "demo")
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------------ pages
    @app.route("/dashboard")
    @login_required
    def dashboard():
        if g.user["role"] == "creator":
            subs = query("SELECT s.*, u.name AS creator_name FROM submissions s JOIN users u ON u.id=s.creator_id "
                         "WHERE s.creator_id=? ORDER BY s.updated_at DESC, s.id DESC", (g.user["id"],))
        else:
            subs = query("SELECT s.*, u.name AS creator_name FROM submissions s JOIN users u ON u.id=s.creator_id "
                         "ORDER BY s.updated_at DESC, s.id DESC")
        rows = [dict(s, **{"c": counts(s)}) for s in subs]
        awaiting = [r for r in rows if r["status"] == "in_review" and r["creator_id"] != g.user["id"]] \
            if g.user["role"] in REVIEW_ROLES else []
        return render_template("dashboard.html", rows=rows, awaiting=awaiting)

    @app.route("/about")
    def about():
        return render_template("about.html", model=app.config["ANTHROPIC_MODEL"])

    @app.route("/submissions/new", methods=["GET", "POST"])
    @login_required
    @role_required("creator")
    def new_submission():
        if request.method == "POST":
            title = request.form.get("title", "").strip()[:120]
            channel = request.form.get("channel", "")
            intent = request.form.get("intent", "").strip()[:300]
            text = request.form.get("text", "").replace("\r\n", "\n").strip()
            if not title or not text or channel not in CHANNELS:
                flash("Please add a title, choose a channel and paste the content.", "error")
            elif len(text) > 20000:
                flash("Content is too long for this prototype (20,000 characters maximum).", "error")
            else:
                sid = create_submission(g.user["id"], title, channel, intent, text)
                flash("Review complete. Go through each finding below.", "ok")
                return redirect(url_for("submission", sid=sid))
        return render_template("new.html", samples={k: dict(zip(("title", "channel", "intent", "text"), v))
                                                    for k, v in seed.SAMPLES.items()})

    @app.route("/submissions/<int:sid>")
    @login_required
    def submission(sid):
        sub = load_sub(sid)
        versions = query("SELECT number, note, created_at FROM versions WHERE submission_id=? ORDER BY number", (sid,))
        try:
            vnum = int(request.args.get("v", sub["current_version"]))
        except ValueError:
            vnum = sub["current_version"]
        ver = query("SELECT * FROM versions WHERE submission_id=? AND number=?", (sid, vnum), one=True)
        if not ver:
            abort(404)
        review = latest_review(sid, vnum)
        findings = [dict(f) for f in query("SELECT * FROM findings WHERE review_id=? ORDER BY span_start",
                                           (review["id"],))] if review else []
        stats = json.loads(review["stats"]) if review else {}
        is_current = vnum == sub["current_version"]
        can_edit = is_owner_editor(sub) and is_current
        c = {"open": 0, "accepted": 0, "edited": 0, "disputed": 0, "high_open": 0}
        applyable = 0
        for f in findings:
            c[f["status"]] += 1
            if f["severity"] == "high" and f["status"] == "open":
                c["high_open"] += 1
            if (f["status"] == "accepted" and f["suggestion"] is not None) or \
                    (f["status"] == "edited" and f["edited_text"] is not None):
                applyable += 1
        by_id = {f["id"]: f for f in findings}
        comments = [dict(x, excerpt=(by_id[x["finding_id"]]["excerpt"] if x["finding_id"] in by_id else None))
                    for x in query("SELECT c.*, u.name, u.role FROM comments c JOIN users u ON u.id=c.user_id "
                                   "WHERE c.submission_id=? ORDER BY c.id", (sid,))]
        can_decide = (sub["status"] == "in_review" and g.user["role"] in REVIEW_ROLES
                      and sub["creator_id"] != g.user["id"])
        return render_template("submission.html", sub=sub, versions=versions, vnum=vnum, ver=ver, review=review,
                               findings=findings, stats=stats, is_current=is_current, can_edit=can_edit, c=c,
                               applyable=applyable, comments=comments, can_decide=can_decide,
                               content_html=highlight(ver["text"], findings),
                               can_submit=can_edit and c["high_open"] == 0)

    def own_editable_finding(fid):
        f = query("SELECT * FROM findings WHERE id=?", (fid,), one=True)
        if not f:
            abort(404)
        sub = load_sub(f["submission_id"])
        if not is_owner_editor(sub) or f["version_number"] != sub["current_version"]:
            abort(403)
        return f, sub

    @app.post("/findings/<int:fid>/respond")
    @login_required
    def respond(fid):
        f, sub = own_editable_finding(fid)
        action = request.form.get("action", "")
        t = now()
        if action == "accept":
            execute("UPDATE findings SET status='accepted', responded_by=?, responded_at=? WHERE id=?", (g.user["id"], t, fid))
        elif action == "edit":
            txt = request.form.get("text", "")[:2000]
            execute("UPDATE findings SET status='edited', edited_text=?, responded_by=?, responded_at=? WHERE id=?",
                    (txt, g.user["id"], t, fid))
        elif action == "dispute":
            reason = request.form.get("reason", "").strip()[:500]
            if len(reason) < 5:
                flash("Please explain briefly why you disagree (at least a few words).", "error")
                return redirect(url_for("submission", sid=sub["id"]) + f"#finding-{fid}")
            execute("UPDATE findings SET status='disputed', dispute_reason=?, responded_by=?, responded_at=? WHERE id=?",
                    (reason, g.user["id"], t, fid))
        elif action == "reopen":
            execute("UPDATE findings SET status='open', edited_text=NULL, dispute_reason=NULL WHERE id=?", (fid,))
        else:
            abort(400)
        audit(g.user["id"], f"finding_{action}", f"{f['category']}: {f['excerpt'][:60]}", sub["id"])
        return redirect(url_for("submission", sid=sub["id"]) + f"#finding-{fid}")

    @app.post("/submissions/<int:sid>/apply")
    @login_required
    def apply_changes(sid):
        sub = load_sub(sid)
        if not is_owner_editor(sub):
            abort(403)
        review = latest_review(sid, sub["current_version"])
        ver = query("SELECT * FROM versions WHERE submission_id=? AND number=?", (sid, sub["current_version"]), one=True)
        edits = []
        for f in query("SELECT * FROM findings WHERE review_id=?", (review["id"],)):
            if f["status"] == "accepted" and f["suggestion"] is not None:
                edits.append((f["span_start"], f["span_end"], f["suggestion"]))
            elif f["status"] == "edited" and f["edited_text"] is not None:
                edits.append((f["span_start"], f["span_end"], f["edited_text"]))
        new_text, n = engine.apply_edits(ver["text"], edits)
        if n == 0:
            flash("There are no accepted or edited changes to apply yet.", "error")
            return redirect(url_for("submission", sid=sid))
        newv = sub["current_version"] + 1
        t = now()
        execute("INSERT INTO versions(submission_id, number, text, note, created_by, created_at) VALUES (?,?,?,?,?,?)",
                (sid, newv, new_text, f"Applied {n} change(s) chosen by the creator", g.user["id"], t))
        execute("UPDATE submissions SET current_version=?, status='draft', updated_at=? WHERE id=?", (newv, t, sid))
        audit(g.user["id"], "changes_applied", f"{n} change(s) -> v{newv}", sid)
        run_review(sid, newv, g.user["id"], carry_from=sub["current_version"])
        flash(f"Version {newv} created with {n} change(s) you chose, and re-checked.", "ok")
        return redirect(url_for("submission", sid=sid))

    @app.post("/submissions/<int:sid>/rerun")
    @login_required
    def rerun(sid):
        sub = load_sub(sid)
        if not is_owner_editor(sub):
            abort(403)
        run_review(sid, sub["current_version"], g.user["id"])
        flash("Review re-run with the current knowledge base. Your earlier responses were kept.", "ok")
        return redirect(url_for("submission", sid=sid))

    @app.post("/submissions/<int:sid>/submit")
    @login_required
    def submit_for_review(sid):
        sub = load_sub(sid)
        if not is_owner_editor(sub):
            abort(403)
        if counts(sub)["high_open"]:
            flash("Respond to every high-severity finding (accept, edit or dispute) before submitting.", "error")
            return redirect(url_for("submission", sid=sid))
        execute("UPDATE submissions SET status='in_review', updated_at=?, decision_note=NULL WHERE id=?", (now(), sid))
        audit(g.user["id"], "submitted_for_approval", f"v{sub['current_version']}", sid)
        flash("Submitted for approval. A manager or reviewer will decide.", "ok")
        return redirect(url_for("submission", sid=sid))

    @app.post("/submissions/<int:sid>/comment")
    @login_required
    def comment(sid):
        sub = load_sub(sid)
        body = request.form.get("body", "").strip()[:2000]
        fid = request.form.get("finding_id") or None
        if not body:
            flash("Write a comment first.", "error")
            return redirect(url_for("submission", sid=sid) + "#comments")
        if fid and not query("SELECT 1 FROM findings WHERE id=? AND submission_id=?", (fid, sid), one=True):
            fid = None
        execute("INSERT INTO comments(submission_id, finding_id, user_id, body, created_at) VALUES (?,?,?,?,?)",
                (sid, fid, g.user["id"], body, now()))
        execute("UPDATE submissions SET updated_at=? WHERE id=?", (now(), sid))
        audit(g.user["id"], "comment_added", body[:80], sid)
        return redirect(url_for("submission", sid=sid) + "#comments")

    @app.post("/submissions/<int:sid>/decide")
    @login_required
    def decide(sid):
        sub = load_sub(sid)
        if g.user["role"] not in REVIEW_ROLES or sub["creator_id"] == g.user["id"]:
            abort(403)
        if sub["status"] != "in_review":
            flash("This submission is not waiting for a decision.", "error")
            return redirect(url_for("submission", sid=sid))
        decision = request.form.get("decision", "")
        note = request.form.get("note", "").strip()[:1000]
        mapping = {"approve": "approved", "changes": "changes_requested", "reject": "rejected"}
        if decision not in mapping:
            abort(400)
        if decision != "approve" and len(note) < 3:
            flash("Please add a short note explaining what needs to change.", "error")
            return redirect(url_for("submission", sid=sid) + "#decision")
        execute("UPDATE submissions SET status=?, decision_note=?, decided_by=?, updated_at=? WHERE id=?",
                (mapping[decision], note, g.user["id"], now(), sid))
        audit(g.user["id"], f"decision_{mapping[decision]}", note[:100], sid)
        flash(f"Decision recorded: {STATUS_LABEL[mapping[decision]]}.", "ok")
        return redirect(url_for("submission", sid=sid))

    @app.route("/submissions/<int:sid>/history")
    @login_required
    def history(sid):
        sub = load_sub(sid)
        vers = query("SELECT v.*, u.name FROM versions v JOIN users u ON u.id=v.created_by WHERE submission_id=? ORDER BY number",
                     (sid,))
        items = []
        for i, v in enumerate(vers):
            items.append({"v": v, "diff": diff_html(vers[i - 1]["text"], v["text"]) if i else None})
        events = query("SELECT a.*, u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id "
                       "WHERE a.submission_id=? ORDER BY a.id", (sid,))
        return render_template("history.html", sub=sub, items=items, events=events)

    # ------------------------------------------------------------------ knowledge base
    @app.route("/kb")
    @login_required
    def kb():
        groups = {}
        for r in query("SELECT * FROM kb_items ORDER BY kind, id"):
            item = {**dict(r), **json.loads(r["extra"] or "{}")}
            groups.setdefault(r["kind"], []).append(item)
        return render_template("kb.html", groups=groups, kinds=KB_KINDS, topics=TOPICS,
                               is_admin=g.user["role"] == "admin")

    @app.post("/kb/save")
    @login_required
    @role_required("admin")
    def kb_save():
        f = request.form
        kind, title, body = f.get("kind", ""), f.get("title", "").strip()[:200], f.get("body", "").strip()[:20000]
        if kind not in KB_KINDS or not title:
            flash("Please fill in the title.", "error")
            return redirect(url_for("kb"))
        if kind == "guideline":
            topic = f.get("topic", "general")
            extra = {"topic": topic if topic in TOPICS else "general"}
        elif kind == "fact":
            extra = {"keywords": f.get("keywords", "").strip()[:300], "value": f.get("value", "").strip()[:100]}
            if not extra["keywords"] or not extra["value"]:
                flash("A fact needs trigger keywords and an approved value (for example '32 countries').", "error")
                return redirect(url_for("kb"))
        elif kind == "term":
            extra = {"banned": f.get("banned", "").strip()[:300]}
        elif kind == "asset":
            if not engine.HEX6.search(body):
                flash("A palette entry needs a colour like #1F6F5B in the value box.", "error")
                return redirect(url_for("kb"))
            extra = {"type": "color"}
        else:
            extra = {}
        iid = f.get("id")
        if iid and query("SELECT 1 FROM kb_items WHERE id=? AND kind=?", (iid, kind), one=True):
            execute("UPDATE kb_items SET title=?, body=?, extra=?, updated_by=?, updated_at=? WHERE id=?",
                    (title, body, json.dumps(extra), g.user["id"], now(), iid))
        else:
            execute("INSERT INTO kb_items(kind, title, body, extra, updated_by, updated_at) VALUES (?,?,?,?,?,?)",
                    (kind, title, body, json.dumps(extra), g.user["id"], now()))
        audit(g.user["id"], "kb_saved", f"{kind}: {title}")
        flash("Saved. Existing reviews are unchanged until a creator uses 'Re-run review'.", "ok")
        return redirect(url_for("kb"))

    @app.post("/kb/<int:iid>/delete")
    @login_required
    @role_required("admin")
    def kb_delete(iid):
        row = query("SELECT * FROM kb_items WHERE id=?", (iid,), one=True)
        if row:
            execute("DELETE FROM kb_items WHERE id=?", (iid,))
            audit(g.user["id"], "kb_deleted", f"{row['kind']}: {row['title']}")
            flash("Deleted.", "ok")
        return redirect(url_for("kb"))

    @app.post("/kb/import")
    @login_required
    @role_required("admin")
    def kb_import():
        file = request.files.get("file")
        title = request.form.get("title", "").strip()[:200]
        topic = request.form.get("topic", "general")
        if not file or not file.filename.lower().endswith((".txt", ".md")):
            flash("Please choose a .txt or .md file (PDF and Word import are planned for Round 3).", "error")
            return redirect(url_for("kb"))
        try:
            body = file.read().decode("utf-8").strip()[:20000]
        except UnicodeDecodeError:
            flash("That file is not UTF-8 text.", "error")
            return redirect(url_for("kb"))
        if not body:
            flash("The file is empty.", "error")
            return redirect(url_for("kb"))
        execute("INSERT INTO kb_items(kind, title, body, extra, updated_by, updated_at) VALUES (?,?,?,?,?,?)",
                ("guideline", title or file.filename, body, json.dumps({"topic": topic if topic in TOPICS else "general"}),
                 g.user["id"], now()))
        audit(g.user["id"], "kb_imported", file.filename)
        flash("Guideline imported.", "ok")
        return redirect(url_for("kb"))

    # ------------------------------------------------------------------ database init + demo data
    with app.app_context():
        con = dbm.get_db()
        con.executescript(dbm.SCHEMA)
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seed.seed_users_and_kb(con)
            creator = query("SELECT id FROM users WHERE role='creator'", one=True)["id"]
            for key, status in (("social", "draft"), ("press", "draft"), ("clean", "in_review")):
                title, channel, intent, text = seed.SAMPLES[key]
                sid = create_submission(creator, title, channel, intent, text, use_ai=False)
                if status != "draft":
                    execute("UPDATE submissions SET status=? WHERE id=?", (status, sid))
                    audit(creator, "submitted_for_approval", "demo data", sid)
    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", "5000")))
