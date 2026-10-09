from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "nirikshan.db"


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Open, commit/rollback, and always close a SQLite connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    with connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS buildings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                ward TEXT,
                age_years INTEGER,
                previous_category TEXT,
                notes TEXT,
                pincode TEXT,
                latitude REAL,
                longitude REAL,
                structure_type TEXT NOT NULL DEFAULT 'Unknown',
                floor_count INTEGER,
                primary_material TEXT NOT NULL DEFAULT 'Unknown',
                repair_history TEXT NOT NULL DEFAULT 'Unknown',
                previous_engineer_assessment TEXT NOT NULL DEFAULT 'No verified record',
                verified_strength_data TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_id INTEGER NOT NULL,
                capture_point TEXT NOT NULL,
                image_path TEXT NOT NULL,
                captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                quality_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'accepted',
                is_baseline INTEGER NOT NULL DEFAULT 0,
                reject_reason TEXT,
                image_hash TEXT,
                FOREIGN KEY(building_id) REFERENCES buildings(id)
            );

            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                building_id INTEGER NOT NULL,
                capture_point TEXT NOT NULL,
                previous_capture_id INTEGER NOT NULL,
                current_capture_id INTEGER NOT NULL,
                baseline_capture_id INTEGER,
                score REAL NOT NULL,
                band TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                factors_json TEXT NOT NULL,
                result_dir TEXT NOT NULL,
                is_reliable INTEGER NOT NULL DEFAULT 1,
                review_status TEXT NOT NULL DEFAULT 'Pending',
                review_note TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(building_id) REFERENCES buildings(id)
            );
            """
        )
        # Forward-compatible migration for databases created by earlier project builds.
        _add_column_if_missing(conn, "captures", "status", "TEXT NOT NULL DEFAULT 'accepted'")
        _add_column_if_missing(conn, "captures", "is_baseline", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "buildings", "pincode", "TEXT")
        _add_column_if_missing(conn, "buildings", "latitude", "REAL")
        _add_column_if_missing(conn, "buildings", "longitude", "REAL")
        _add_column_if_missing(conn, "buildings", "structure_type", "TEXT NOT NULL DEFAULT 'Unknown'")
        _add_column_if_missing(conn, "buildings", "floor_count", "INTEGER")
        _add_column_if_missing(conn, "buildings", "primary_material", "TEXT NOT NULL DEFAULT 'Unknown'")
        _add_column_if_missing(conn, "buildings", "repair_history", "TEXT NOT NULL DEFAULT 'Unknown'")
        _add_column_if_missing(conn, "buildings", "previous_engineer_assessment", "TEXT NOT NULL DEFAULT 'No verified record'")
        _add_column_if_missing(conn, "buildings", "verified_strength_data", "TEXT")
        _add_column_if_missing(conn, "captures", "reject_reason", "TEXT")
        _add_column_if_missing(conn, "captures", "image_hash", "TEXT")
        _add_column_if_missing(conn, "analyses", "baseline_capture_id", "INTEGER")
        _add_column_if_missing(conn, "analyses", "is_reliable", "INTEGER NOT NULL DEFAULT 1")
        _add_column_if_missing(conn, "analyses", "archived", "INTEGER NOT NULL DEFAULT 0")


def rows(query: str, args: tuple = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(row) for row in conn.execute(query, args).fetchall()]


def row(query: str, args: tuple = ()) -> dict[str, Any] | None:
    with connection() as conn:
        result = conn.execute(query, args).fetchone()
        return dict(result) if result else None


def execute(query: str, args: tuple = ()) -> int:
    with connection() as conn:
        cur = conn.execute(query, args)
        return int(cur.lastrowid)


def update(query: str, args: tuple = ()) -> None:
    with connection() as conn:
        conn.execute(query, args)


def decode_json_fields(item: dict[str, Any] | None, fields: list[str]) -> dict[str, Any] | None:
    if not item:
        return item
    for field in fields:
        if field in item and isinstance(item[field], str):
            try:
                item[field] = json.loads(item[field])
            except json.JSONDecodeError:
                pass
    return item
