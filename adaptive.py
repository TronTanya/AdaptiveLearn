"""Rule-based adaptive difficulty logic for AdaptiveLearn.

This module has no Streamlit dependencies so it can be tested in isolation.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

MIN_LEVEL = 1
MAX_LEVEL = 3
START_LEVEL = 2
STREAK_TO_LEVEL_UP = 2
SESSION_LENGTH = 10

LEVEL_NAMES = {1: "лёгкая", 2: "средняя", 3: "сложная"}

QUESTIONS_PATH = Path(__file__).parent / "content" / "questions.json"


class AnswerAlreadySubmitted(RuntimeError):
    """Raised when an answer is submitted twice for the same question."""


class SessionFinished(RuntimeError):
    """Raised when an action requires an active question but the session is over."""


def load_bank(path: Path | str = QUESTIONS_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class AdaptiveState:
    target_level: int = START_LEVEL
    streak: int = 0


def clamp_level(level: int) -> int:
    return max(MIN_LEVEL, min(MAX_LEVEL, level))


def update_state(state: AdaptiveState, is_correct: bool) -> AdaptiveState:
    """Apply the adaptation rules to one answer and return the new state.

    - two consecutive correct answers raise the target level by one;
    - one incorrect answer lowers the target level by one;
    - the level stays within [MIN_LEVEL, MAX_LEVEL];
    - the streak resets after an incorrect answer or a level change.
    """
    if not is_correct:
        return AdaptiveState(target_level=clamp_level(state.target_level - 1), streak=0)

    streak = state.streak + 1
    if streak >= STREAK_TO_LEVEL_UP:
        new_level = clamp_level(state.target_level + 1)
        if new_level != state.target_level:
            return AdaptiveState(target_level=new_level, streak=0)
        # Already at the top level: keep counting, there is no level change.
        return AdaptiveState(target_level=new_level, streak=streak)
    return AdaptiveState(target_level=state.target_level, streak=streak)


def select_question(
    questions: Iterable[dict[str, Any]],
    used_ids: set[str],
    target_level: int,
    rng: random.Random,
) -> Optional[dict[str, Any]]:
    """Pick an unused question closest to the target level.

    Ties in distance are resolved in favour of the lower difficulty.
    Within the chosen difficulty the question is picked with ``rng``,
    so the same seed and answer sequence reproduce the same selection.
    """
    available = [q for q in questions if q["id"] not in used_ids]
    if not available:
        return None
    best_level = min(
        {q["difficulty"] for q in available},
        key=lambda d: (abs(d - target_level), d),
    )
    candidates = sorted(
        (q for q in available if q["difficulty"] == best_level), key=lambda q: q["id"]
    )
    return rng.choice(candidates)


@dataclass
class AnswerRecord:
    number: int
    question_id: str
    selected_index: int
    is_correct: bool
    difficulty: int
    target_before: int
    target_after: int


@dataclass
class TrainingSession:
    """State of one training session, independent of the UI."""

    questions: list[dict[str, Any]]
    topic: str
    seed: int
    length: int = SESSION_LENGTH
    state: AdaptiveState = field(default_factory=AdaptiveState)
    records: list[AnswerRecord] = field(default_factory=list)
    current: Optional[dict[str, Any]] = None
    awaiting_next: bool = False
    finished: bool = False
    stopped_early: bool = False

    def __post_init__(self) -> None:
        self.questions = [q for q in self.questions if q["topic"] == self.topic]
        self._rng = random.Random(self.seed)
        self._used: set[str] = set()
        self._pick_next()

    @property
    def target_level(self) -> int:
        return self.state.target_level

    @property
    def question_number(self) -> int:
        """1-based number of the current question."""
        return len(self.records) + (0 if self.awaiting_next else 1)

    def _pick_next(self) -> None:
        if len(self.records) >= self.length:
            self.current = None
            self.finished = True
            return
        q = select_question(self.questions, self._used, self.state.target_level, self._rng)
        if q is None:
            self.current = None
            self.finished = True
            return
        self._used.add(q["id"])
        self.current = q

    def submit(self, selected_index: int) -> AnswerRecord:
        if self.finished or self.current is None:
            raise SessionFinished("Сессия уже завершена")
        if self.awaiting_next:
            raise AnswerAlreadySubmitted("Ответ на этот вопрос уже принят")
        if not 0 <= selected_index < len(self.current["options"]):
            raise ValueError("Недопустимый вариант ответа")

        is_correct = selected_index == self.current["correct"]
        before = self.state.target_level
        self.state = update_state(self.state, is_correct)
        record = AnswerRecord(
            number=len(self.records) + 1,
            question_id=self.current["id"],
            selected_index=selected_index,
            is_correct=is_correct,
            difficulty=self.current["difficulty"],
            target_before=before,
            target_after=self.state.target_level,
        )
        self.records.append(record)
        self.awaiting_next = True
        return record

    def advance(self) -> None:
        if self.finished:
            return
        if not self.awaiting_next:
            raise RuntimeError("Сначала нужно ответить на текущий вопрос")
        self.awaiting_next = False
        self._pick_next()

    def stop(self) -> None:
        """Finish early; an unanswered current question is discarded."""
        if not self.finished and len(self.records) < self.length:
            self.stopped_early = True
        self.finished = True
        self.awaiting_next = False
        self.current = None

    @property
    def completed(self) -> bool:
        """True if the session ended without early termination."""
        return self.finished and not self.stopped_early

    def question_by_id(self, qid: str) -> dict[str, Any]:
        return next(q for q in self.questions if q["id"] == qid)

    def summary(self) -> dict[str, Any]:
        answered = len(self.records)
        correct = sum(r.is_correct for r in self.records)
        by_level = {lvl: 0 for lvl in range(MIN_LEVEL, MAX_LEVEL + 1)}
        for r in self.records:
            by_level[r.difficulty] += 1
        return {
            "answered": answered,
            "correct": correct,
            "accuracy": (correct / answered * 100) if answered else 0.0,
            "by_level": by_level,
            "mistakes": [r for r in self.records if not r.is_correct],
            "final_target": self.state.target_level,
            "completed": self.completed,
        }
