"""Shared database utilities for SQLite operations."""

import os
import sqlite3


def init_sqlite_db(db_path: str, schema_sql: str, indexes_sql: str = "") -> sqlite3.Connection:
    """Initialize SQLite database with optional indexes.

    Args:
        db_path: Path to SQLite database file
        schema_sql: SQL CREATE TABLE statement(s)
        indexes_sql: SQL CREATE INDEX statement(s) (optional)

    Returns:
        SQLite connection object
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(schema_sql)
    if indexes_sql:
        conn.executescript(indexes_sql)
    return conn
