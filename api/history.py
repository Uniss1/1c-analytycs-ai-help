"""Chat history and response cache (SQLite)."""

import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    created_at  TEXT NOT NULL,
    last_active TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    intent      TEXT,
    register    TEXT,
    query_text  TEXT,
    latency_ms  INTEGER,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS response_cache (
    id              INTEGER PRIMARY KEY,
    question_hash   TEXT NOT NULL UNIQUE,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    intent          TEXT NOT NULL,
    dashboard_slug  TEXT,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_hash ON response_cache(question_hash);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON response_cache(expires_at);
"""


def init_history(db_path: str) -> None:
    """Connect to history.db and create tables if needed."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(db_path)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys = ON")
    _conn.executescript(_SCHEMA)
    _conn.commit()


def _get_conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("Call init_history(db_path) first")
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(user_id: str | None = None) -> str:
    """Create a new chat session, return session_id."""
    conn = _get_conn()
    session_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, last_active) VALUES (?, ?, ?, ?)",
        (session_id, user_id, now, now),
    )
    conn.commit()
    return session_id


def save_message(
    session_id: str,
    role: str,
    content: str,
    intent: str | None = None,
    register: str | None = None,
    query_text: str | None = None,
    latency_ms: int | None = None,
) -> None:
    """Save a message to chat history."""
    conn = _get_conn()
    now = _now()
    conn.execute(
        """INSERT INTO messages
           (session_id, role, content, intent, register, query_text, latency_ms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, role, content, intent, register, query_text, latency_ms, now),
    )
    conn.execute(
        "UPDATE sessions SET last_active = ? WHERE id = ?",
        (now, session_id),
    )
    conn.commit()


def get_recent_messages(session_id: str, limit: int = 4) -> list[dict]:
    """Get last N messages for context."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT role, content FROM messages
           WHERE session_id = ?
           ORDER BY created_at DESC LIMIT ?""",
        (session_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _question_hash(question: str, dashboard_slug: str | None) -> str:
    normalized = question.strip().lower()
    key = f"{normalized}|{dashboard_slug or ''}"
    return hashlib.sha256(key.encode()).hexdigest()


def check_cache(question: str, dashboard_slug: str | None = None) -> dict | None:
    """Check response cache. Returns {answer, intent} or None."""
    conn = _get_conn()
    now = _now()
    qhash = _question_hash(question, dashboard_slug)
    row = conn.execute(
        """SELECT answer, intent FROM response_cache
           WHERE question_hash = ? AND expires_at > ?""",
        (qhash, now),
    ).fetchone()
    if row:
        return {"answer": row["answer"], "intent": row["intent"]}
    return None


def save_cache(
    question: str,
    answer: str,
    intent: str,
    dashboard_slug: str | None = None,
    ttl_minutes: int = 60,
) -> None:
    """Save response to cache with TTL."""
    conn = _get_conn()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ttl_minutes)
    qhash = _question_hash(question, dashboard_slug)
    conn.execute(
        """INSERT OR REPLACE INTO response_cache
           (question_hash, question, answer, intent, dashboard_slug, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (qhash, question, answer, intent, dashboard_slug, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
