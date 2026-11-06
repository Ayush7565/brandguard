"""SQLite helpers. One connection per request."""
import os
import sqlite3
from datetime import datetime, timezone

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('creator','manager','reviewer','admin'))
);
CREATE TABLE IF NOT EXISTS kb_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,              -- guideline | fact | term | claim | asset
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  extra TEXT NOT NULL DEFAULT '{}',
  updated_by INTEGER,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  channel TEXT NOT NULL,
  intent TEXT NOT NULL DEFAULT '',
  creator_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft', -- draft | in_review | changes_requested | approved | rejected
  current_version INTEGER NOT NULL DEFAULT 1,
  decision_note TEXT,
  decided_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL,
  number INTEGER NOT NULL,
  text TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(submission_id, number)
);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL,
  version_number INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  ai_status TEXT NOT NULL DEFAULT '',
  stats TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_id INTEGER NOT NULL,
  submission_id INTEGER NOT NULL,
  version_number INTEGER NOT NULL,
  category TEXT NOT NULL,
  severity TEXT NOT NULL,
  span_start INTEGER NOT NULL,
  span_end INTEGER NOT NULL,
  excerpt TEXT NOT NULL,
  explanation TEXT NOT NULL,
  source_kind TEXT,
  source_id INTEGER,
  source_label TEXT,
  source_quote TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  suggestion TEXT,
  advice TEXT,
  detected_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',  -- open | accepted | edited | disputed
  edited_text TEXT,
  dispute_reason TEXT,
  responded_by INTEGER,
  responded_at TEXT
);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL,
  finding_id INTEGER,
  user_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  user_id INTEGER,
  submission_id INTEGER,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ai_usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  user_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_id);
CREATE INDEX IF NOT EXISTS idx_sub_creator ON submissions(creator_id);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def audit(user_id, action, detail="", submission_id=None):
    execute("INSERT INTO audit(ts, user_id, submission_id, action, detail) VALUES (?,?,?,?,?)",
            (now(), user_id, submission_id, action, detail[:500]))
