"""SQLite persistence for AdaptiveLearn sessions and answers."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = Path(
    os.environ.get("ADAPTIVELEARN_DB", Path(__file__).parent / "data" / "adaptivelearn.db")
)

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_INCOMPLETE = "incomplete"

STATUS_NAMES = {
    STATUS_IN_PROGRESS: "Не завершена",
    STATUS_COMPLETED: "Завершена",
    STATUS_INCOMPLETE: "Незавершённая (досрочно)",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    topic         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'incomplete')),
    seed          INTEGER NOT NULL,
    is_demo       INTEGER NOT NULL DEFAULT 0,
    final_target  INTEGER
);

CREATE TABLE IF NOT EXISTS answers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_number  INTEGER NOT NULL,
    question_id      TEXT NOT NULL,
    selected_index   INTEGER NOT NULL,
    selected_answer  TEXT NOT NULL,
    is_correct       INTEGER NOT NULL,
    difficulty       INTEGER NOT NULL,
    target_before    INTEGER NOT NULL,
    target_after     INTEGER NOT NULL,
    answered_at      TEXT NOT NULL,
    UNIQUE (session_id, question_number),
    UNIQUE (session_id, question_id)
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    with closing(connect(db_path)) as conn, conn:
        conn.executescript(SCHEMA)


def create_session(
    session_id: str,
    topic: str,
    seed: int,
    is_demo: bool = False,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    with closing(connect(db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO sessions (id, topic, started_at, status, seed, is_demo) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, topic, _now(), STATUS_IN_PROGRESS, seed, int(is_demo)),
        )


def save_answer(
    session_id: str,
    question_number: int,
    question_id: str,
    selected_index: int,
    selected_answer: str,
    is_correct: bool,
    difficulty: int,
    target_before: int,
    target_after: int,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> bool:
    """Store one answer. Returns False if this answer was already stored."""
    try:
        with closing(connect(db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO answers (session_id, question_number, question_id, "
                "selected_index, selected_answer, is_correct, difficulty, "
                "target_before, target_after, answered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    question_number,
                    question_id,
                    selected_index,
                    selected_answer,
                    int(is_correct),
                    difficulty,
                    target_before,
                    target_after,
                    _now(),
                ),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def finish_session(
    session_id: str,
    completed: bool,
    final_target: int,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    status = STATUS_COMPLETED if completed else STATUS_INCOMPLETE
    with closing(connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE sessions SET status = ?, finished_at = ?, final_target = ? "
            "WHERE id = ? AND status = ?",
            (status, _now(), final_target, session_id, STATUS_IN_PROGRESS),
        )


def get_session(session_id: str, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[dict]:
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def get_sessions(demo: bool = False, db_path: Path | str = DEFAULT_DB_PATH) -> list[dict]:
    """Sessions that contain at least one answer, newest first."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT s.*, COUNT(a.id) AS answered, COALESCE(SUM(a.is_correct), 0) AS correct "
            "FROM sessions s JOIN answers a ON a.session_id = s.id "
            "WHERE s.is_demo = ? GROUP BY s.id ORDER BY s.started_at DESC, s.rowid DESC",
            (int(demo),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_answers(
    session_id: Optional[str] = None,
    demo: bool = False,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict]:
    query = (
        "SELECT a.*, s.topic, s.status, s.seed, s.is_demo, s.started_at "
        "FROM answers a JOIN sessions s ON s.id = a.session_id WHERE s.is_demo = ?"
    )
    params: list[Any] = [int(demo)]
    if session_id is not None:
        query += " AND a.session_id = ?"
        params.append(session_id)
    query += " ORDER BY s.started_at, s.rowid, a.question_number"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_overview(demo: bool = False, db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    with closing(connect(db_path)) as conn:
        completed = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE is_demo = ? AND status = ?",
            (int(demo), STATUS_COMPLETED),
        ).fetchone()[0]
        answered, correct = conn.execute(
            "SELECT COUNT(a.id), COALESCE(SUM(a.is_correct), 0) "
            "FROM answers a JOIN sessions s ON s.id = a.session_id WHERE s.is_demo = ?",
            (int(demo),),
        ).fetchone()
    return {
        "completed_sessions": completed,
        "answered": answered,
        "correct": correct,
        "accuracy": (correct / answered * 100) if answered else None,
    }


def get_topic_stats(demo: bool = False, db_path: Path | str = DEFAULT_DB_PATH) -> list[dict]:
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT s.topic AS topic, COUNT(a.id) AS answered, SUM(a.is_correct) AS correct "
            "FROM answers a JOIN sessions s ON s.id = a.session_id "
            "WHERE s.is_demo = ? GROUP BY s.topic ORDER BY s.topic",
            (int(demo),),
        ).fetchall()
    return [
        {**dict(r), "accuracy": r["correct"] / r["answered"] * 100} for r in rows
    ]


CSV_COLUMNS = [
    ("session_id", "ID сессии"),
    ("topic", "Тема"),
    ("started_at", "Начало сессии"),
    ("status", "Статус сессии"),
    ("seed", "Seed"),
    ("question_number", "Номер вопроса"),
    ("question_id", "ID вопроса"),
    ("selected_answer", "Выбранный ответ"),
    ("is_correct", "Верно"),
    ("difficulty", "Сложность вопроса"),
    ("target_before", "Целевой уровень до"),
    ("target_after", "Целевой уровень после"),
    ("answered_at", "Время ответа"),
]


def answers_to_csv(rows: list[dict], topic_names: Optional[dict] = None) -> bytes:
    """CSV in UTF-8 with BOM and ';' separator for Russian Excel."""
    topic_names = topic_names or {}
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow([title for _, title in CSV_COLUMNS])
    for r in rows:
        values = []
        for key, _ in CSV_COLUMNS:
            v = r.get(key)
            if key == "topic":
                v = topic_names.get(v, v)
            elif key == "status":
                v = STATUS_NAMES.get(v, v)
            elif key == "is_correct":
                v = "да" if v else "нет"
            values.append(v)
        writer.writerow(values)
    return buf.getvalue().encode("utf-8-sig")
