import random

import pytest

from adaptive import (
    START_LEVEL,
    AdaptiveState,
    AnswerAlreadySubmitted,
    SessionFinished,
    TrainingSession,
    load_bank,
    select_question,
    update_state,
)

BANK = load_bank()["questions"]


def make_questions(levels):
    """Minimal synthetic bank: one question per entry in `levels`."""
    return [
        {"id": f"q{i}", "topic": "t", "difficulty": d, "options": ["a", "b", "c", "d"], "correct": 0}
        for i, d in enumerate(levels)
    ]


def wrong_index(question):
    return (question["correct"] + 1) % 4


# ---------- update_state ----------

def test_initial_level_is_medium():
    assert AdaptiveState().target_level == START_LEVEL == 2
    session = TrainingSession(BANK, topic="loops", seed=1)
    assert session.target_level == 2
    assert session.current["difficulty"] == 2


def test_two_correct_answers_increase_level():
    s = update_state(AdaptiveState(), True)
    assert (s.target_level, s.streak) == (2, 1)
    s = update_state(s, True)
    assert (s.target_level, s.streak) == (3, 0)


def test_one_incorrect_answer_decreases_level():
    s = update_state(AdaptiveState(target_level=2), False)
    assert s.target_level == 1


def test_level_stays_within_bounds():
    s = AdaptiveState(target_level=3)
    for _ in range(6):
        s = update_state(s, True)
    assert s.target_level == 3
    s = AdaptiveState(target_level=1)
    for _ in range(3):
        s = update_state(s, False)
    assert s.target_level == 1


def test_streak_resets_after_incorrect_answer():
    s = update_state(AdaptiveState(), True)
    assert s.streak == 1
    s = update_state(s, False)
    assert s.streak == 0
    # A single correct answer after the reset must not raise the level.
    s = update_state(s, True)
    assert (s.target_level, s.streak) == (1, 1)


def test_streak_resets_after_level_change():
    s = update_state(update_state(AdaptiveState(target_level=1), True), True)
    assert (s.target_level, s.streak) == (2, 0)
    s = update_state(s, True)
    assert (s.target_level, s.streak) == (2, 1)


# ---------- select_question ----------

def test_selects_target_level_when_available():
    qs = make_questions([1, 2, 3])
    assert select_question(qs, set(), 3, random.Random(0))["difficulty"] == 3


def test_fallback_to_nearest_level():
    qs = make_questions([1, 1, 3])
    q = select_question(qs, {"q2"}, 3, random.Random(0))
    assert q["difficulty"] == 1  # level 3 exhausted, level 2 absent


def test_fallback_tie_prefers_lower_level():
    qs = make_questions([1, 3])
    for seed in range(20):
        assert select_question(qs, set(), 2, random.Random(seed))["difficulty"] == 1


def test_returns_none_when_exhausted():
    qs = make_questions([1, 2])
    assert select_question(qs, {"q0", "q1"}, 2, random.Random(0)) is None


# ---------- TrainingSession ----------

def play(session, pattern):
    """Answer according to a pattern of booleans; returns asked question ids."""
    asked = []
    for correct in pattern:
        if session.finished:
            break
        q = session.current
        asked.append(q["id"])
        session.submit(q["correct"] if correct else wrong_index(q))
        session.advance()
    return asked


def test_no_repeated_questions_in_session():
    session = TrainingSession(BANK, topic="variables", seed=42)
    asked = play(session, [True, False] * 5)
    assert len(asked) == 10
    assert len(set(asked)) == 10
    assert session.finished and session.completed


def test_session_finishes_gracefully_when_bank_exhausted():
    qs = make_questions([1, 2, 3])
    session = TrainingSession(qs, topic="t", seed=0, length=10)
    asked = play(session, [True] * 10)
    assert sorted(asked) == ["q0", "q1", "q2"]
    assert session.finished and session.current is None
    with pytest.raises(SessionFinished):
        session.submit(0)


def test_seeded_selection_is_reproducible():
    pattern = [True, True, False, True, False, False, True, True, True, False]
    a = play(TrainingSession(BANK, topic="conditions", seed=123), pattern)
    b = play(TrainingSession(BANK, topic="conditions", seed=123), pattern)
    assert a == b
    others = {tuple(play(TrainingSession(BANK, topic="conditions", seed=s), pattern)) for s in range(10)}
    assert len(others) > 1  # the seed actually influences the selection


def test_duplicate_submission_is_rejected():
    session = TrainingSession(BANK, topic="loops", seed=7)
    session.submit(session.current["correct"])
    with pytest.raises(AnswerAlreadySubmitted):
        session.submit(session.current["correct"])
    assert len(session.records) == 1
    assert session.state.streak == 1


def test_records_keep_actual_and_target_difficulty_separate():
    session = TrainingSession(BANK, topic="loops", seed=3)
    play(session, [True, True, False])
    r1, r2, r3 = session.records
    assert (r1.target_before, r1.target_after, r1.difficulty) == (2, 2, 2)
    assert (r2.target_before, r2.target_after, r2.difficulty) == (2, 3, 2)
    assert (r3.target_before, r3.target_after, r3.difficulty) == (3, 2, 3)


def test_early_stop_marks_session_incomplete():
    session = TrainingSession(BANK, topic="loops", seed=5)
    play(session, [True, False, True])
    session.stop()
    summary = session.summary()
    assert session.finished and not session.completed
    assert summary["answered"] == 3 and summary["correct"] == 2
    assert len(summary["mistakes"]) == 1
    assert sum(summary["by_level"].values()) == 3
