"""
TSLA Alpha Engine: Schema Migration
Idempotent — safe to run multiple times.
"""
import os
import sqlite3

DB_PATH = os.path.expanduser("~/tsla_alpha.db")


def migrate(db_path: str = DB_PATH):
    # Ensure base schema exists first
    import sys, os as _os
    sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from data.init_db import init_db
    conn = init_db(db_path)

    for col, typ in [("loss_tag", "VARCHAR(64)"), ("loss_notes", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE closed_trades ADD COLUMN {col} {typ}")
            conn.commit()
            print(f"  + added column closed_trades.{col}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"  . closed_trades.{col} already exists")
            else:
                raise

    conn.close()
    print("migration complete")


if __name__ == "__main__":
    migrate()
