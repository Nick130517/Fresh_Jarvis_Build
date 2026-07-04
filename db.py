"""
db.py — SQLite data layer for Jarvis.

Everything Jarvis remembers lives in one local SQLite file:
  - projects        : the registry (uni/cyber work, side hustles, hobbies, general)
  - project_notes    : freeform log entries against a project ("completed room X")
  - project_stats    : numeric time-series data pulled from connectors or logged manually
  - memory           : general, non-project-scoped facts and preferences

Design goal: adding a new project or a new stat should never require a schema
change — it's all rows, not columns. Same philosophy as the PreeceStudio
theme-JSON pattern: extend by adding data, not by editing code.
"""

import sqlite3
import datetime
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "jarvis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL DEFAULT 'general',   -- cybersecurity | side-hustle | hobby | general
    status      TEXT NOT NULL DEFAULT 'active',     -- active | paused | done
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    note        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,     -- e.g. 'sales_count', 'revenue_gbp', 'rooms_completed', 'streak_days'
    value       REAL NOT NULL,
    source      TEXT DEFAULT 'manual',   -- 'manual' | 'github' | 'etsy' | 'tryhackme' | ...
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_project ON project_notes(project_id);
CREATE INDEX IF NOT EXISTS idx_stats_project ON project_stats(project_id, metric);
"""


def _now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------- projects

def create_project(name: str, category: str = "general", description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, category, status, description, created_at, updated_at) "
            "VALUES (?, ?, 'active', ?, ?, ?)",
            (name, category, description, _now(), _now()),
        )
        return cur.lastrowid


def get_project_by_name(name: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()


def list_projects(status: str | None = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY updated_at DESC", (status,)
            ).fetchall()
        return conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()


def set_project_status(name: str, status: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE projects SET status = ?, updated_at = ? WHERE name = ? COLLATE NOCASE",
            (status, _now(), name),
        )
        return cur.rowcount > 0


def touch_project(project_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE id = ?", (_now(), project_id)
        )


# ------------------------------------------------------------------- notes

def add_note(project_name: str, note: str) -> bool:
    project = get_project_by_name(project_name)
    if not project:
        return False
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO project_notes (project_id, note, created_at) VALUES (?, ?, ?)",
            (project["id"], note, _now()),
        )
    touch_project(project["id"])
    return True


def get_recent_notes(project_name: str, limit: int = 10) -> list[sqlite3.Row]:
    project = get_project_by_name(project_name)
    if not project:
        return []
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM project_notes WHERE project_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (project["id"], limit),
        ).fetchall()


# ------------------------------------------------------------------- stats

def log_stat(project_name: str, metric: str, value: float, source: str = "manual") -> bool:
    project = get_project_by_name(project_name)
    if not project:
        return False
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO project_stats (project_id, metric, value, source, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project["id"], metric, value, source, _now()),
        )
    touch_project(project["id"])
    return True


def get_latest_stats(project_name: str) -> dict:
    """Latest value per metric for a project, e.g. {'revenue_gbp': 42.0, 'sales_count': 3}"""
    project = get_project_by_name(project_name)
    if not project:
        return {}
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT metric, value, recorded_at FROM project_stats s1
            WHERE project_id = ? AND recorded_at = (
                SELECT MAX(recorded_at) FROM project_stats s2
                WHERE s2.project_id = s1.project_id AND s2.metric = s1.metric
            )
            """,
            (project["id"],),
        ).fetchall()
    return {r["metric"]: r["value"] for r in rows}


def get_stat_history(project_name: str, metric: str, limit: int = 30) -> list[sqlite3.Row]:
    project = get_project_by_name(project_name)
    if not project:
        return []
    with get_conn() as conn:
        return conn.execute(
            "SELECT value, recorded_at FROM project_stats "
            "WHERE project_id = ? AND metric = ? ORDER BY recorded_at DESC LIMIT ?",
            (project["id"], metric, limit),
        ).fetchall()


# ------------------------------------------------------------------ memory

def remember(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO memory (key, value, created_at) VALUES (?, ?, ?)",
            (key, value, _now()),
        )


def recall(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM memory WHERE key = ? ORDER BY created_at DESC LIMIT 1", (key,)
        ).fetchone()
    return row["value"] if row else None


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {DB_PATH}")
