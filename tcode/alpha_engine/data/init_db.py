"""
TSLA Alpha Engine: SQLite Database Initializer
Creates ~/tsla_alpha.db with all required tables (idempotent).
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/tsla_alpha.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Create all tables if they don't exist. Returns open connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


if __name__ == "__main__":
    conn = init_db()
    conn.close()
    print(f"DB initialized at {DB_PATH}")
