import json
import os
import sqlite3
from datetime import datetime

DB_PATH = "data/memory.db"

def init_db():
    """Creates the memory database if it doesn't exist."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context (
            thread_id TEXT PRIMARY KEY,
            last_sql TEXT,
            last_question TEXT,
            last_data_json TEXT,
            last_chart_type TEXT,
            last_filters TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_message(thread_id: str, role: str, content: str, metadata: dict = None):
    """Saves a message to persistent memory."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO conversations (thread_id, role, content, metadata) VALUES (?, ?, ?, ?)",
        (thread_id, role, content, json.dumps(metadata or {}))
    )
    conn.commit()
    conn.close()

def get_history(thread_id: str, limit: int = 10) -> list:
    """Gets recent conversation history for a thread."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT role, content, metadata, created_at 
           FROM conversations 
           WHERE thread_id = ? 
           ORDER BY created_at DESC LIMIT ?""",
        (thread_id, limit)
    ).fetchall()
    conn.close()
    return [
        {
            "role": r[0],
            "content": r[1],
            "metadata": json.loads(r[2]),
            "created_at": r[3]
        }
        for r in reversed(rows)
    ]

def save_context(thread_id: str, **kwargs):
    """Saves query context for a thread — last SQL, data, filters etc."""
    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute(
        "SELECT thread_id FROM context WHERE thread_id = ?", (thread_id,)
    ).fetchone()

    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        sets += ", updated_at = CURRENT_TIMESTAMP"
        values = list(kwargs.values()) + [thread_id]
        conn.execute(f"UPDATE context SET {sets} WHERE thread_id = ?", values)
    else:
        kwargs["thread_id"] = thread_id
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" * len(kwargs))
        conn.execute(
            f"INSERT INTO context ({cols}) VALUES ({placeholders})",
            list(kwargs.values())
        )
    conn.commit()
    conn.close()

def get_context(thread_id: str) -> dict:
    """Gets the saved context for a thread."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        """SELECT last_sql, last_question, last_data_json, 
                  last_chart_type, last_filters
           FROM context WHERE thread_id = ?""",
        (thread_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {}
    return {
        "last_sql": row[0],
        "last_question": row[1],
        "last_data_json": row[2],
        "last_chart_type": row[3],
        "last_filters": row[4]
    }

def clear_thread(thread_id: str):
    """Clears all memory for a thread."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM conversations WHERE thread_id = ?", (thread_id,))
    conn.execute("DELETE FROM context WHERE thread_id = ?", (thread_id,))
    conn.commit()
    conn.close()

def get_all_threads() -> list:
    """Lists all conversation threads."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """SELECT thread_id, COUNT(*) as messages, MAX(created_at) as last_active
           FROM conversations GROUP BY thread_id ORDER BY last_active DESC"""
    ).fetchall()
    conn.close()
    return [{"thread_id": r[0], "messages": r[1], "last_active": r[2]} for r in rows]

# Initialize on import
init_db()