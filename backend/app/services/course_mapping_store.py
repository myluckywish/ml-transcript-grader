from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.services.course_taxonomy import CANONICAL_SUBJECTS, canonicalize_subject, normalize_course_title, seed_mappings

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "screening.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_store() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id TEXT NOT NULL DEFAULT '',
                normalized_title TEXT NOT NULL,
                raw_title_example TEXT NOT NULL,
                subject TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(school_id, normalized_title)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS unknown_course_titles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id TEXT NOT NULL DEFAULT '',
                raw_title TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                seen_count INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'open',
                resolution_subject TEXT,
                resolution_note TEXT,
                UNIQUE(school_id, normalized_title)
            )
            """
        )
        conn.commit()

    _migrate_subject_taxonomy()
    _seed_defaults()


def _migrate_subject_taxonomy() -> None:
    replacements = {
        "math": "mathematics",
        "science": "natural_sciences",
        "social_studies": "social_sciences",
        "foreign_language": "other_units",
        "electives": "other_units",
    }
    with _connect() as conn:
        for old_value, new_value in replacements.items():
            conn.execute("UPDATE course_mappings SET subject = ? WHERE subject = ?", (new_value, old_value))
            conn.execute(
                "UPDATE unknown_course_titles SET resolution_subject = ? WHERE resolution_subject = ?",
                (new_value, old_value),
            )
        conn.commit()


def _seed_defaults() -> None:
    with _connect() as conn:
        for raw_title, subject in seed_mappings():
            normalized_title = normalize_course_title(raw_title)
            conn.execute(
                """
                INSERT INTO course_mappings (school_id, normalized_title, raw_title_example, subject, confidence, source)
                VALUES ('', ?, ?, ?, 1.0, 'seed')
                ON CONFLICT(school_id, normalized_title) DO NOTHING
                """,
                (normalized_title, raw_title, subject),
            )
        conn.commit()


def upsert_course_mapping(
    raw_title: str,
    subject: str,
    school_id: str = "",
    source: str = "manual",
    confidence: float = 1.0,
) -> dict[str, Any]:
    canonical_subject = canonicalize_subject(subject)
    if canonical_subject not in CANONICAL_SUBJECTS:
        raise ValueError(f"Invalid subject '{subject}'. Must be one of: {', '.join(CANONICAL_SUBJECTS)}")

    normalized_title = normalize_course_title(raw_title)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO course_mappings (school_id, normalized_title, raw_title_example, subject, confidence, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(school_id, normalized_title) DO UPDATE SET
                raw_title_example=excluded.raw_title_example,
                subject=excluded.subject,
                confidence=excluded.confidence,
                source=excluded.source,
                updated_at=CURRENT_TIMESTAMP
            """,
            (school_id, normalized_title, raw_title, canonical_subject, confidence, source),
        )
        row = conn.execute(
            """
            SELECT id, school_id, normalized_title, raw_title_example, subject, confidence, source, created_at, updated_at
            FROM course_mappings
            WHERE school_id = ? AND normalized_title = ?
            """,
            (school_id, normalized_title),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def find_course_mapping(normalized_title: str, school_id: str = "") -> dict[str, Any] | None:
    with _connect() as conn:
        if school_id:
            school_row = conn.execute(
                """
                SELECT * FROM course_mappings
                WHERE school_id = ? AND normalized_title = ?
                """,
                (school_id, normalized_title),
            ).fetchone()
            if school_row is not None:
                payload = dict(school_row)
                subject = canonicalize_subject(str(payload.get("subject", "")))
                if subject:
                    payload["subject"] = subject
                return payload

        global_row = conn.execute(
            """
            SELECT * FROM course_mappings
            WHERE school_id = '' AND normalized_title = ?
            """,
            (normalized_title,),
        ).fetchone()
    if global_row is None:
        return None
    payload = dict(global_row)
    subject = canonicalize_subject(str(payload.get("subject", "")))
    if subject:
        payload["subject"] = subject
    return payload


def queue_unknown_title(raw_title: str, normalized_title: str, school_id: str = "") -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO unknown_course_titles (school_id, raw_title, normalized_title)
            VALUES (?, ?, ?)
            ON CONFLICT(school_id, normalized_title) DO UPDATE SET
                last_seen_at=CURRENT_TIMESTAMP,
                seen_count=unknown_course_titles.seen_count + 1,
                status='open',
                resolution_subject=NULL,
                resolution_note=NULL
            """,
            (school_id, raw_title, normalized_title),
        )
        row = conn.execute(
            """
            SELECT * FROM unknown_course_titles
            WHERE school_id = ? AND normalized_title = ?
            """,
            (school_id, normalized_title),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def list_unknown_titles(school_id: str = "", status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(500, limit))
    with _connect() as conn:
        if school_id:
            rows = conn.execute(
                """
                SELECT * FROM unknown_course_titles
                WHERE school_id = ? AND status = ?
                ORDER BY seen_count DESC, last_seen_at DESC
                LIMIT ?
                """,
                (school_id, status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM unknown_course_titles
                WHERE status = ?
                ORDER BY seen_count DESC, last_seen_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def resolve_unknown_title(
    unknown_id: int,
    subject: str,
    note: str = "",
    create_mapping: bool = True,
) -> dict[str, Any]:
    canonical_subject = canonicalize_subject(subject)
    if canonical_subject not in CANONICAL_SUBJECTS:
        raise ValueError(f"Invalid subject '{subject}'. Must be one of: {', '.join(CANONICAL_SUBJECTS)}")

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM unknown_course_titles WHERE id = ?",
            (unknown_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown queue id '{unknown_id}' not found.")

        conn.execute(
            """
            UPDATE unknown_course_titles
            SET status = 'resolved',
                resolution_subject = ?,
                resolution_note = ?,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (canonical_subject, note, unknown_id),
        )
        updated = conn.execute(
            "SELECT * FROM unknown_course_titles WHERE id = ?",
            (unknown_id,),
        ).fetchone()
        conn.commit()

    result = dict(updated) if updated else {}
    if create_mapping:
        upsert_course_mapping(
            raw_title=result.get("raw_title", ""),
            subject=canonical_subject or "",
            school_id=result.get("school_id", ""),
            source="manual_resolution",
            confidence=1.0,
        )
    return result
