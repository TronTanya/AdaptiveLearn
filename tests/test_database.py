import csv
import io

import pytest

import database as db
from adaptive import TrainingSession, load_bank

BANK = load_bank()["questions"]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    db.init_db(path)
    return path


def save_record(path, session_id, training, record):
    q = training.question_by_id(record.question_id)
    return db.save_answer(
        session_id,
        question_number=record.number,
        question_id=record.question_id,
        selected_index=record.selected_index,
        selected_answer=q["options"][record.selected_index],
        is_correct=record.is_correct,
        difficulty=record.difficulty,
        target_before=record.target_before,
        target_after=record.target_after,
        db_path=path,
    )


def run_session(path, session_id, topic, pattern, seed=11, demo=False, stop_after=None):
    training = TrainingSession(BANK, topic=topic, seed=seed)
    db.create_session(session_id, topic, seed, is_demo=demo, db_path=path)
    for i, correct in enumerate(pattern):
        q = training.current
        record = training.submit(q["correct"] if correct else (q["correct"] + 1) % 4)
        assert save_record(path, session_id, training, record)
        if stop_after is not None and i + 1 == stop_after:
            training.stop()
            break
        training.advance()
    db.finish_session(session_id, training.completed, training.target_level, db_path=path)
    return training


def test_init_is_idempotent(db_path):
    db.init_db(db_path)
    assert db.get_overview(db_path=db_path)["answered"] == 0


def test_session_and_answers_persist(db_path):
    pattern = [True, True, False, True, True, True, False, False, True, True]
    training = run_session(db_path, "s1", "loops", pattern, seed=99)

    session = db.get_session("s1", db_path=db_path)
    assert session["status"] == db.STATUS_COMPLETED
    assert session["seed"] == 99
    assert session["finished_at"] is not None
    assert session["final_target"] == training.target_level

    rows = db.get_answers(session_id="s1", db_path=db_path)
    assert len(rows) == 10
    for row, record in zip(rows, training.records):
        assert row["question_id"] == record.question_id
        assert row["selected_index"] == record.selected_index
        assert bool(row["is_correct"]) == record.is_correct
        assert row["difficulty"] == record.difficulty
        assert row["target_before"] == record.target_before
        assert row["target_after"] == record.target_after


def test_duplicate_answer_not_stored(db_path):
    training = TrainingSession(BANK, topic="loops", seed=1)
    db.create_session("s1", "loops", 1, db_path=db_path)
    record = training.submit(training.current["correct"])
    assert save_record(db_path, "s1", training, record) is True
    assert save_record(db_path, "s1", training, record) is False
    assert len(db.get_answers(session_id="s1", db_path=db_path)) == 1


def test_incomplete_session_status(db_path):
    run_session(db_path, "s1", "conditions", [True, False, True], stop_after=3)
    assert db.get_session("s1", db_path=db_path)["status"] == db.STATUS_INCOMPLETE
    overview = db.get_overview(db_path=db_path)
    assert overview["completed_sessions"] == 0
    assert overview["answered"] == 3


def test_statistics_and_demo_separation(db_path):
    run_session(db_path, "a", "loops", [True] * 10)
    run_session(db_path, "b", "variables", [True, False] * 5)
    run_session(db_path, "demo", "loops", [False] * 10, demo=True)

    overview = db.get_overview(db_path=db_path)
    assert overview == {"completed_sessions": 2, "answered": 20, "correct": 15, "accuracy": 75.0}

    topics = {r["topic"]: r for r in db.get_topic_stats(db_path=db_path)}
    assert topics["loops"]["answered"] == 10 and topics["loops"]["accuracy"] == 100.0
    assert topics["variables"]["correct"] == 5

    demo = db.get_overview(demo=True, db_path=db_path)
    assert demo["answered"] == 10 and demo["correct"] == 0
    assert {s["id"] for s in db.get_sessions(db_path=db_path)} == {"a", "b"}


def test_csv_export_has_bom_and_russian_headers(db_path):
    run_session(db_path, "s1", "loops", [True, False])
    data = db.answers_to_csv(db.get_answers(db_path=db_path), {"loops": "Циклы"})
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert rows[0][0] == "ID сессии"
    assert len(rows) == 1 + 2
    assert rows[1][1] == "Циклы"
