import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "import.db")  # module-level app uses a temp db

import app as app_module  # noqa: E402
import db as dbm  # noqa: E402


def make_client(**cfg):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    app = app_module.create_app({"DATABASE": path, "TESTING": True, "CSRF_ENABLED": False, **cfg})
    return app, app.test_client()


def login(client, role):
    return client.post(f"/demo-login/{role}", follow_redirects=True)


class Workflow(unittest.TestCase):
    def test_full_creator_to_manager_flow(self):
        app, c = make_client()
        r = login(c, "creator")
        self.assertIn(b"Spring drop on Instagram", r.data)

        # open the problem-filled draft (id 1) and see cited findings
        page = c.get("/submissions/1").data.decode()
        self.assertIn("Source: Claims and promises", page)
        self.assertIn("Approved fact: countries", page)
        # cannot submit while high-severity findings are open
        c.post("/submissions/1/submit", follow_redirects=True)
        db = dbm.connect(app.config["DATABASE"])
        self.assertEqual(db.execute("SELECT status FROM submissions WHERE id=1").fetchone()[0], "draft")

        # accept fixes where there is a suggestion; dispute the rest of the high ones
        rows = db.execute("SELECT id, severity, suggestion FROM findings WHERE submission_id=1 AND version_number=1").fetchall()
        for fid, sev, sug in rows:
            if sug is not None:
                c.post(f"/findings/{fid}/respond", data={"action": "accept"})
            elif sev == "high":
                c.post(f"/findings/{fid}/respond", data={"action": "dispute", "reason": "Alt text added in the design tool"})
        # a dispute without a reason is refused
        any_id = rows[0][0]
        c.post(f"/findings/{any_id}/respond", data={"action": "dispute", "reason": "no"})

        # apply -> new version, text actually changed, nothing else touched
        c.post("/submissions/1/apply", follow_redirects=True)
        v = {n: t for n, t in db.execute("SELECT number, text FROM versions WHERE submission_id=1")}
        self.assertEqual(sorted(v), [1, 2])
        self.assertIn("bottle", v[2])
        self.assertNotIn("flask", v[2])
        self.assertIn("32 countries", v[2])
        self.assertIn("2018", v[2])
        self.assertIn("#E86A4A", v[2])
        self.assertIn("Meet the", v[2])  # creator's own words are preserved

        # disputed findings carry over to the re-check of v2
        disputed = db.execute("SELECT COUNT(*) FROM findings WHERE submission_id=1 AND version_number=2 AND status='disputed'").fetchone()[0]
        self.assertGreaterEqual(disputed, 1)
        highs = db.execute("SELECT COUNT(*) FROM findings f WHERE submission_id=1 AND version_number=2 AND severity='high' AND status='open'"
                           " AND review_id=(SELECT MAX(id) FROM reviews WHERE submission_id=1 AND version_number=2)").fetchone()[0]
        self.assertEqual(highs, 0)

        # submit for approval
        c.post("/submissions/1/submit", follow_redirects=True)
        self.assertEqual(db.execute("SELECT status FROM submissions WHERE id=1").fetchone()[0], "in_review")

        # creator cannot decide on their own content
        self.assertEqual(c.post("/submissions/1/decide", data={"decision": "approve"}).status_code, 403)

        # manager sees it, must give a note to reject, then approves
        c.post("/logout")
        login(c, "manager")
        self.assertIn(b"Waiting for your decision", c.get("/dashboard").data)
        c.post("/submissions/1/decide", data={"decision": "reject", "note": ""})
        self.assertEqual(db.execute("SELECT status FROM submissions WHERE id=1").fetchone()[0], "in_review")
        c.post("/submissions/1/comment", data={"body": "Please double-check the alt text."})
        c.post("/submissions/1/decide", data={"decision": "approve", "note": ""})
        self.assertEqual(db.execute("SELECT status FROM submissions WHERE id=1").fetchone()[0], "approved")

        # history and audit trail exist
        h = c.get("/submissions/1/history").data.decode()
        self.assertIn("changes applied", h)
        self.assertIn("decision approved", h)
        self.assertIn("<del>", h)
        actions = [r[0] for r in db.execute("SELECT action FROM audit WHERE submission_id=1")]
        for a in ("submission_created", "review_run", "finding_accept", "changes_applied", "submitted_for_approval", "comment_added"):
            self.assertIn(a, actions)

    def test_changes_requested_returns_to_creator(self):
        app, c = make_client()
        login(c, "manager")
        c.post("/submissions/3/decide", data={"decision": "changes", "note": "Add a hero image."})
        db = dbm.connect(app.config["DATABASE"])
        self.assertEqual(db.execute("SELECT status FROM submissions WHERE id=3").fetchone()[0], "changes_requested")
        c.post("/logout")
        login(c, "creator")
        self.assertIn(b"Add a hero image.", c.get("/submissions/3").data)
        self.assertIn(b"Submit for approval", c.get("/submissions/3").data)

    def test_roles_and_permissions(self):
        app, c = make_client()
        self.assertEqual(c.get("/dashboard").status_code, 302)  # login required
        login(c, "manager")
        self.assertEqual(c.get("/submissions/new").status_code, 403)  # only creators submit
        self.assertEqual(c.post("/kb/save", data={"kind": "term", "title": "x"}).status_code, 403)  # only admin edits KB
        # manager cannot change a creator's finding
        self.assertEqual(c.post("/findings/1/respond", data={"action": "accept"}).status_code, 403)
        c.post("/logout")
        login(c, "admin")
        c.post("/kb/save", data={"kind": "term", "title": "tumbler", "banned": "cup", "body": "Say tumbler."})
        self.assertIn(b"tumbler", c.get("/kb").data)

    def test_new_submission_runs_review_and_kb_changes_apply_on_rerun(self):
        app, c = make_client()
        login(c, "creator")
        r = c.post("/submissions/new", data={"title": "T", "channel": "social", "intent": "sell",
                                             "text": "Our cup is great."}, follow_redirects=True)
        self.assertIn(b"No issues found", r.data)
        c.post("/logout")
        login(c, "admin")
        c.post("/kb/save", data={"kind": "term", "title": "bottle", "banned": "cup", "body": "Say bottle."})
        c.post("/logout")
        login(c, "creator")
        r = c.post("/submissions/4/rerun", follow_redirects=True)
        self.assertIn(b"Terminology: bottle", r.data)

    def test_xss_is_escaped(self):
        app, c = make_client()
        login(c, "creator")
        r = c.post("/submissions/new", data={"title": "<script>alert(1)</script>", "channel": "social",
                                             "text": "<img src=x onerror=alert(1)> flask"}, follow_redirects=True)
        self.assertNotIn(b"<script>alert(1)</script>", r.data)
        self.assertNotIn(b"<img src=x", r.data)

    def test_csrf_is_enforced(self):
        app, c = make_client(CSRF_ENABLED=True)
        self.assertEqual(c.post("/login", data={"email": "a", "password": "b"}).status_code, 400)

    def test_ai_layer_is_rate_limited_and_degrades_gracefully(self):
        app, c = make_client(ANTHROPIC_API_KEY="test-key", AI_USER_HOURLY_LIMIT=1)
        login(c, "creator")
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"content": [{"type": "text", "text": "[]"}]}
        with mock.patch("requests.post", return_value=ok) as post:
            c.post("/submissions/new", data={"title": "A", "channel": "social", "text": "Hello there."})
            c.post("/submissions/new", data={"title": "B", "channel": "social", "text": "Hello again."})
            self.assertEqual(post.call_count, 1)  # second call blocked by the hourly limit
        db = dbm.connect(app.config["DATABASE"])
        statuses = [r[0] for r in db.execute("SELECT ai_status FROM reviews ORDER BY id DESC LIMIT 2")]
        self.assertTrue(statuses[0].startswith("paused"))
        self.assertEqual(statuses[1], "used")

    def test_ai_outage_does_not_break_review(self):
        app, c = make_client(ANTHROPIC_API_KEY="test-key")
        login(c, "creator")
        with mock.patch("requests.post", side_effect=ConnectionError()):
            r = c.post("/submissions/new", data={"title": "A", "channel": "social", "text": "Our flask is guaranteed to work."},
                       follow_redirects=True)
        self.assertIn(b"unavailable (ConnectionError)", r.data)
        self.assertIn(b"Terminology", r.data)  # rule-based findings still delivered


if __name__ == "__main__":
    unittest.main()
