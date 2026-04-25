import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "/var/lib/oncall-assistant/oncall.db")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            severity TEXT DEFAULT 'medium',
            source TEXT DEFAULT 'manual',
            status TEXT DEFAULT 'OPEN',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS timeline_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        );

        CREATE TABLE IF NOT EXISTS context_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            data TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (incident_id) REFERENCES incidents(id)
        );
    """)
    conn.commit()
    conn.close()


def create_incident(title, description, severity="medium", source="manual"):
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO incidents (title, description, severity, source, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'OPEN', ?, ?)",
        (title, description, severity, source, now, now)
    )
    incident_id = cur.lastrowid
    conn.commit()
    conn.close()
    return incident_id


def get_incident(incident_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_incidents(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_incident_status(incident_id, status):
    now = datetime.utcnow().isoformat()
    resolved_at = now if status == "RESOLVED" else None
    conn = get_db()
    conn.execute(
        "UPDATE incidents SET status = ?, updated_at = ?, resolved_at = COALESCE(?, resolved_at) WHERE id = ?",
        (status, now, resolved_at, incident_id)
    )
    conn.commit()
    conn.close()


def add_timeline_event(incident_id, event_type, content):
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO timeline_events (incident_id, event_type, content, created_at) VALUES (?, ?, ?, ?)",
        (incident_id, event_type, content, now)
    )
    conn.commit()
    conn.close()


def get_timeline(incident_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM timeline_events WHERE incident_id = ? ORDER BY created_at ASC",
        (incident_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_context(incident_id, source, data):
    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO context_cache (incident_id, source, data, fetched_at) VALUES (?, ?, ?, ?)",
        (incident_id, source, data, now)
    )
    conn.commit()
    conn.close()


def get_context(incident_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM context_cache WHERE incident_id = ?", (incident_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
