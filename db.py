import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nigah.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanned_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            expiry_date TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def add_item(item_type, name, status, expiry_date=None):
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO scanned_items (type, name, status, expiry_date, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            item_type,
            name,
            status,
            expiry_date,
            datetime.now().isoformat(sep=" ", timespec="seconds"),
        ),
    )
    conn.commit()
    item_id = cursor.lastrowid
    conn.close()
    return item_id


def update_item(item_id, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{column} = ?" for column in fields)
    values = list(fields.values()) + [item_id]
    conn = get_connection()
    conn.execute(
        f"UPDATE scanned_items SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()


def get_items():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM scanned_items ORDER BY timestamp DESC, id DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_item(item_id):
    conn = get_connection()
    cursor = conn.execute("DELETE FROM scanned_items WHERE id = ?", (item_id,))
    conn.commit()
    changed = cursor.rowcount
    conn.close()
    return changed > 0
