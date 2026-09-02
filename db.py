import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nigah.db")

# update_item() builds its SET clause from these names, so a field that is not
# on this list can never reach SQL - not even accidentally from a caller.
_UPDATABLE_COLUMNS = ("name", "status", "expiry_date", "mfg_date")

# A scan can hold a writer open for seconds while gunicorn serves other
# requests against the same file; SQLite's default 5s busy timeout surfaced as
# "database is locked" and silently lost the row.
_CONNECT_TIMEOUT_SECONDS = 30


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=_CONNECT_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    # WAL lets readers proceed while a scan is writing. Idempotent, so it also
    # upgrades a database file created before this was set.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scanned_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                expiry_date TEXT,
                mfg_date TEXT,
                is_mock INTEGER NOT NULL DEFAULT 0,
                timestamp TEXT NOT NULL
            )
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(scanned_items)")}
        if "mfg_date" not in columns:
            conn.execute("ALTER TABLE scanned_items ADD COLUMN mfg_date TEXT")
        # Databases created before this column existed still need it; DEFAULT 0
        # marks every already-stored row as a real detection.
        if "is_mock" not in columns:
            conn.execute(
                "ALTER TABLE scanned_items ADD COLUMN is_mock INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def add_item(item_type, name, status, expiry_date=None, mfg_date=None, is_mock=False):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO scanned_items "
            "(type, name, status, expiry_date, mfg_date, is_mock, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_type, name, status, expiry_date, mfg_date, int(bool(is_mock)), _now()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_item(item_id, **fields):
    unknown = sorted(set(fields) - set(_UPDATABLE_COLUMNS))
    if unknown:
        raise ValueError(f"refusing to update unknown column(s): {', '.join(unknown)}")
    # None means "this scan did not observe that field" - never overwrite data
    # the user already has with it.
    values = {
        column: value for column, value in fields.items() if value is not None
    }
    if not values:
        return 0
    set_clause = ", ".join(f"{column} = ?" for column in values)
    conn = get_connection()
    try:
        cursor = conn.execute(
            f"UPDATE scanned_items SET {set_clause} WHERE id = ?",
            list(values.values()) + [item_id],
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_items():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM scanned_items ORDER BY timestamp DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_item(item_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM scanned_items WHERE id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_item(item_id):
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM scanned_items WHERE id = ?", (item_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
