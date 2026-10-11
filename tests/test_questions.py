import contextlib
import io
from collections import Counter

import pytest

from adaptive import load_bank

BANK = load_bank()
QUESTIONS = BANK["questions"]
REQUIRED_FIELDS = {
    "id", "topic", "difficulty", "question", "code",
    "options", "correct", "explanation", "recommendation",
}


def test_total_count():
    assert len(QUESTIONS) == 45


def test_unique_ids():
    ids = [q["id"] for q in QUESTIONS]
    assert len(ids) == len(set(ids))


def test_topic_and_difficulty_counts():
    assert set(BANK["topics"]) == {"variables", "conditions", "loops"}
    counts = Counter((q["topic"], q["difficulty"]) for q in QUESTIONS)
    for topic in BANK["topics"]:
        for level in (1, 2, 3):
            assert counts[(topic, level)] == 5, (topic, level)


@pytest.mark.parametrize("q", QUESTIONS, ids=lambda q: q["id"])
def test_question_structure(q):
    assert REQUIRED_FIELDS <= q.keys()
    assert q["topic"] in BANK["topics"]
    assert q["difficulty"] in (1, 2, 3)
    for key in ("question", "explanation", "recommendation"):
        assert isinstance(q[key], str) and q[key].strip()
    assert q["code"] is None or (isinstance(q["code"], str) and q["code"].strip())
    assert len(q["options"]) == 4
    assert all(isinstance(o, str) and o.strip() for o in q["options"])
    assert len({o.strip() for o in q["options"]}) == 4, "options must be distinct"
    assert isinstance(q["correct"], int) and 0 <= q["correct"] < 4


def run_snippet(code: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "<snippet>", "exec"), {})
    return buf.getvalue()


OUTPUT_QUESTIONS = [
    q for q in QUESTIONS
    if q["code"] and (q["question"] == "Что выведет этот код?" or "expected_output" in q)
]


@pytest.mark.parametrize("q", OUTPUT_QUESTIONS, ids=lambda q: q["id"])
def test_output_questions_match_real_python(q):
    """Execute the snippet and check that only the marked answer matches the real output.

    Questions with descriptive options ("a, затем b") store the literal
    output in ``expected_output``.
    """
    correct = q["options"][q["correct"]]
    if "expected_output" in q:
        assert run_snippet(q["code"]).strip() == q["expected_output"]
        return
    if correct.startswith("Ошибка "):
        error_name = correct.split()[1]
        with pytest.raises(Exception) as exc:
            run_snippet(q["code"])
        assert type(exc.value).__name__ == error_name
        return
    output = run_snippet(q["code"]).strip()
    assert output == correct
    for i, option in enumerate(q["options"]):
        if i != q["correct"]:
            assert option != output


def test_snippets_compile():
    for q in QUESTIONS:
        if q["code"]:
            compile(q["code"], q["id"], "exec")
