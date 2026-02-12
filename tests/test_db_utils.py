"""Tests for db_utils module."""

import os
import tempfile
from addon.oig-proxy.db_utils import init_sqlite_db


def test_init_sqlite_db_with_schema_and_indexes(tmpdir):
    """Test init_sqlite_db with both schema and indexes."""
    db_path = str(tmpdir.join("test.db"))
    
    schema_sql = """
        CREATE TABLE IF NOT EXISTS test_table (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            value INTEGER
        );
    """
    
    indexes_sql = """
        CREATE INDEX IF NOT EXISTS idx_name ON test_table(name);
    """
    
    conn = init_sqlite_db(db_path, schema_sql, indexes_sql)
    
    assert conn is not None
    assert os.path.exists(db_path)
    
    # Verify table was created
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'")
    tables = cursor.fetchall()
    assert len(tables) == 1
    assert tables[0][0] == "test_table"
    
    # Verify index was created
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_name'")
    indexes = cursor.fetchall()
    assert len(indexes) == 1
    assert indexes[0][0] == "idx_name"
    
    conn.close()


def test_init_sqlite_db_with_schema_only(tmpdir):
    """Test init_sqlite_db with schema only (no indexes)."""
    db_path = str(tmpdir.join("test2.db"))
    
    schema_sql = """
        CREATE TABLE IF NOT EXISTS test_table2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
    """
    
    conn = init_sqlite_db(db_path, schema_sql)
    
    assert conn is not None
    assert os.path.exists(db_path)
    
    # Verify table was created
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_table2'")
    tables = cursor.fetchall()
    assert len(tables) == 1
    
    # Verify no extra indexes were created
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = cursor.fetchall()
    # SQLite creates automatic index on PRIMARY KEY
    assert len(indexes) == 1
    
    conn.close()


def test_init_sqlite_db_creates_directory(tmpdir):
    """Test that init_sqlite_db creates parent directory."""
    db_path = str(tmpdir.join("subdir").join("test3.db"))
    
    schema_sql = "CREATE TABLE IF NOT EXISTS test (id INTEGER);"
    
    conn = init_sqlite_db(db_path, schema_sql)
    
    assert conn is not None
    assert os.path.exists(os.path.dirname(db_path))
    
    conn.close()
