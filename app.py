"""AdaptiveLearn — Streamlit interface."""

from __future__ import annotations

import random
import re
import uuid
from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

import adaptive
import database as db
from adaptive import LEVEL_NAMES, SESSION_LENGTH, TrainingSession

st.set_page_config(page_title="AdaptiveLearn", layout="wide")

# Width limit and white surfaces for keyed containers (st-key-* classes are
# generated from the documented `key` argument of st.container).
st.html(
    """
    <style>
    [data-testid="stMainBlockContainer"] { max-width: 1050px; padding-top: 2.5rem; }
    [class*="st-key-card_"] { background: #FFFFFF; }
    </style>
    """
)

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
CHART_CONFIG = {"displayModeBar": False}


@st.cache_data
def load_bank() -> dict:
    return adaptive.load_bank()


@st.cache_resource
def init_storage() -> bool:
    db.init_db()
    return True


def fmt_option(text: str) -> str:
    """Render code-like options (no Cyrillic text) in monospace."""
    if CYRILLIC.search(text):
        return text
    return "`" + text.replace("`", "'") + "`"


def level_label(level: int) -> str:
    return f"{level} — {LEVEL_NAMES[level]}"


def fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")


# ---------- session actions (Streamlit callbacks) ----------

def start_session(topic: str, demo: bool) -> None:
    seed = random.SystemRandom().randrange(1, 2**31)
    session_id = uuid.uuid4().hex
    training = TrainingSession(load_bank()["questions"], topic=topic, seed=seed)
    db.create_session(session_id, topic, seed, is_demo=demo)
    st.session_state.training = training
    st.session_state.session_id = session_id
    st.session_state.is_demo = demo
    if training.finished:
        db.finish_session(session_id, completed=True, final_target=training.target_level)


def answer_key(training: TrainingSession) -> str:
    return f"answer_{st.session_state.session_id}_{training.current['id']}"


def submit_answer() -> None:
    training: TrainingSession = st.session_state.training
    if training.finished or training.awaiting_next:
        return
    selected = st.session_state.get(answer_key(training))
    if selected is None:
        st.session_state.notice = "Выберите вариант ответа, затем нажмите «Проверить ответ»."
        return
    question = training.current
    record = training.submit(selected)
    db.save_answer(
        st.session_state.session_id,
        question_number=record.number,
        question_id=record.question_id,
        selected_index=record.selected_index,
        selected_answer=question["options"][record.selected_index],
        is_correct=record.is_correct,
        difficulty=record.difficulty,
        target_before=record.target_before,
        target_after=record.target_after,
    )


def next_question() -> None:
    training: TrainingSession = st.session_state.training
    if not training.awaiting_next:
        return
    training.advance()
    if training.finished:
        db.finish_session(
            st.session_state.session_id, completed=True, final_target=training.target_level
        )


def stop_session() -> None:
    training: TrainingSession = st.session_state.training
    if training.finished:
        return
    training.stop()
    db.finish_session(
        st.session_state.session_id,
        completed=training.completed,
        final_target=training.target_level,
    )


def reset_session() -> None:
    for key in ("training", "session_id", "is_demo", "notice"):
        st.session_state.pop(key, None)


# ---------- chart ----------

def difficulty_chart(rows: list[dict]) -> go.Figure:
    numbers = [r["question_number"] for r in rows]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=numbers,
            y=[r["difficulty"] for r in rows],
            mode="lines",
            line=dict(color="#98A2B3", width=1.5),
            name="Сложность вопроса",
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=numbers,
            y=[r["target_after"] for r in rows],
            mode="lines",
            line=dict(color="#3563E9", width=1.5, dash="dot", shape="hv"),
            name="Целевой уровень после ответа",
        )
    )
    for correct, name, color, symbol in (
        (True, "Верный ответ", "#12B76A", "circle"),
        (False, "Неверный ответ", "#D92D20", "x"),
    ):
        part = [r for r in rows if bool(r["is_correct"]) == correct]
        fig.add_trace(
            go.Scatter(
                x=[r["question_number"] for r in part],
                y=[r["difficulty"] for r in part],
                mode="markers",
                marker=dict(color=color, size=11, symbol=symbol),
                name=name,
                customdata=[r["question_id"] for r in part],
                hovertemplate="Вопрос %{x}<br>Сложность: %{y}<br>ID: %{customdata}<extra></extra>",
            )
        )
    fig.update_layout(
        title=dict(text="Изменение сложности заданий", font=dict(size=16)),
        height=340,
        margin=dict(l=10, r=10, t=50, b=10),
        plot_bgcolor="#FFFFFF",
        paper_bgcolor="#FFFFFF",
        font=dict(color="#202733"),
        legend=dict(orientation="h", y=-0.25),
    )
    fig.update_xaxes(
        title="Номер вопроса", dtick=1, range=[0.5, max(numbers + [SESSION_LENGTH]) + 0.5],
        gridcolor="#E4E7EC",
    )
    fig.update_yaxes(
        title="Сложность", range=[0.6, 3.4], tickvals=[1, 2, 3],
        ticktext=[level_label(i) for i in (1, 2, 3)], gridcolor="#E4E7EC",
    )
    return fig


def records_as_rows(training: TrainingSession) -> list[dict]:
    return [
        {
            "question_number": r.number,
            "question_id": r.question_id,
            "difficulty": r.difficulty,
            "is_correct": r.is_correct,
            "target_after": r.target_after,
        }
        for r in training.records
    ]


# ---------- training tab ----------

def render_start(bank: dict) -> None:
    topics = bank["topics"]
    with st.container(border=True, key="card_start"):
        st.subheader("Новая тренировка")
        st.write(
            f"Сессия состоит из {SESSION_LENGTH} вопросов. Первый вопрос — средней сложности, "
            "дальше сложность подбирается по вашим ответам."
        )
        topic = st.selectbox(
            "Тема", options=list(topics), format_func=lambda k: topics[k]
        )
        demo = st.checkbox(
            "Демонстрационная сессия",
            help="Демонстрационные сессии сохраняются отдельно и не учитываются в статистике ученика.",
        )
        st.button(
            "Начать тренировку", type="primary", on_click=start_session, args=(topic, demo)
        )


def render_question(training: TrainingSession, bank: dict) -> None:
    q = training.current
    topic_name = bank["topics"][training.topic]
    demo_badge = " · :orange-badge[Демонстрация]" if st.session_state.is_demo else ""

    st.markdown(
        f"**{topic_name}** · Вопрос {training.question_number} из {SESSION_LENGTH}"
        f" · :blue-badge[Сложность вопроса: {level_label(q['difficulty'])}]"
        f" · :gray-badge[Целевой уровень: {training.target_level}]{demo_badge}"
    )

    with st.container(border=True, key="card_question"):
        st.markdown(f"##### {q['question']}")
        if q.get("code"):
            st.code(q["code"], language="python")
        st.radio(
            "Варианты ответа",
            options=list(range(len(q["options"]))),
            format_func=lambda i: fmt_option(q["options"][i]),
            index=None,
            key=answer_key(training),
            disabled=training.awaiting_next,
        )

        if training.awaiting_next:
            record = training.records[-1]
            if record.is_correct:
                st.success("Верно.")
            else:
                st.error(
                    f"Неверно. Ваш ответ: {fmt_option(q['options'][record.selected_index])}"
                )
            st.markdown(f"**Правильный ответ:** {fmt_option(q['options'][q['correct']])}")
            st.markdown(f"**Объяснение.** {q['explanation']}")
            if not record.is_correct:
                st.markdown(f"**Рекомендация.** {q['recommendation']}")

            if record.target_after > record.target_before:
                change = (
                    f"Два верных ответа подряд: целевой уровень повышен "
                    f"{record.target_before} → {record.target_after}."
                )
            elif record.target_after < record.target_before:
                change = (
                    f"Неверный ответ: целевой уровень понижен "
                    f"{record.target_before} → {record.target_after}."
                )
            else:
                change = f"Целевой уровень не изменился: {record.target_after}."
            st.caption(change)

        notice = st.session_state.pop("notice", None)
        if notice:
            st.warning(notice)

        with st.container(horizontal=True):
            if training.awaiting_next:
                last = len(training.records) >= SESSION_LENGTH
                st.button(
                    "Показать результаты" if last else "Следующий вопрос",
                    type="primary",
                    on_click=next_question,
                )
            else:
                st.button("Проверить ответ", type="primary", on_click=submit_answer)
            st.button("Завершить досрочно", on_click=stop_session)


def render_results(training: TrainingSession, bank: dict) -> None:
    summary = training.summary()
    topic_name = bank["topics"][training.topic]

    st.subheader("Результаты тренировки")
    labels = [topic_name]
    if st.session_state.is_demo:
        labels.append(":orange-badge[Демонстрационная сессия]")
    st.markdown(" · ".join(labels))

    if not summary["completed"]:
        st.warning("Незавершённая сессия: тренировка остановлена досрочно.")
    elif summary["answered"] < SESSION_LENGTH:
        st.info("Вопросы по теме закончились раньше, чем набралось 10 ответов.")

    if summary["answered"] == 0:
        st.write("Ни на один вопрос не был дан ответ.")
        st.button("Новая тренировка", type="primary", on_click=reset_session)
        return

    with st.container(border=True, key="card_summary"):
        cols = st.columns(3)
        cols[0].metric("Верных ответов", f"{summary['correct']} из {summary['answered']}")
        cols[1].metric("Точность", f"{summary['accuracy']:.0f}%")
        cols[2].metric("Итоговый целевой уровень", level_label(summary["final_target"]))
        st.markdown(
            "**Вопросов по сложности:** "
            + ", ".join(f"{LEVEL_NAMES[lvl]} — {n}" for lvl, n in summary["by_level"].items())
        )

    with st.container(border=True, key="card_session_chart"):
        st.plotly_chart(
            difficulty_chart(records_as_rows(training)),
            width="stretch",
            key="session_chart",
            config=CHART_CONFIG,
        )

    st.markdown("#### Ошибки и объяснения")
    if not summary["mistakes"]:
        st.write("Ошибок нет.")
    for r in summary["mistakes"]:
        q = training.question_by_id(r.question_id)
        with st.expander(f"Вопрос {r.number}: {q['question']} (сложность {r.difficulty})"):
            if q.get("code"):
                st.code(q["code"], language="python")
            st.markdown(f"**Ваш ответ:** {fmt_option(q['options'][r.selected_index])}")
            st.markdown(f"**Правильный ответ:** {fmt_option(q['options'][q['correct']])}")
            st.markdown(f"**Объяснение.** {q['explanation']}")
            st.markdown(f"**Рекомендация.** {q['recommendation']}")

    st.button("Новая тренировка", type="primary", on_click=reset_session)


def render_training(bank: dict) -> None:
    training = st.session_state.get("training")
    if training is None:
        render_start(bank)
    elif training.finished:
        render_results(training, bank)
    else:
        render_question(training, bank)


# ---------- progress tab ----------

def render_progress(bank: dict) -> None:
    topics = bank["topics"]
    dataset = st.segmented_control(
        "Данные",
        options=["learner", "demo"],
        format_func=lambda v: "Учебные сессии" if v == "learner" else "Демонстрационные сессии",
        default="learner",
        key="progress_dataset",
    )
    demo = dataset == "demo"

    overview = db.get_overview(demo=demo)
    if overview["answered"] == 0:
        st.info(
            "Сохранённых ответов пока нет. Пройдите тренировку на вкладке «Тренировка» — "
            "здесь появится статистика."
        )
        return

    with st.container(border=True, key="card_overview"):
        cols = st.columns(3)
        cols[0].metric("Завершённых сессий", overview["completed_sessions"])
        cols[1].metric("Отвечено вопросов", overview["answered"])
        cols[2].metric("Общая точность", f"{overview['accuracy']:.0f}%")
        st.caption(
            "Учитываются ответы из всех сессий выбранного набора, включая незавершённые. "
            "Точность в сессиях с разным набором сложностей напрямую не сравнима: "
            "70% на сложных вопросах и 70% на лёгких означают разное."
        )

    st.markdown("#### Точность по темам")
    st.dataframe(
        [
            {
                "Тема": topics.get(r["topic"], r["topic"]),
                "Ответов": r["answered"],
                "Верных": r["correct"],
                "Точность, %": round(r["accuracy"], 1),
            }
            for r in db.get_topic_stats(demo=demo)
        ],
        hide_index=True,
        width="stretch",
    )

    st.markdown("#### Сессия")
    sessions = db.get_sessions(demo=demo)
    session = st.selectbox(
        "Выберите сессию",
        options=sessions,
        format_func=lambda s: (
            f"{fmt_dt(s['started_at'])} · {topics.get(s['topic'], s['topic'])} · "
            f"{db.STATUS_NAMES[s['status']]} · {s['correct']} из {s['answered']}"
        ),
    )
    rows = db.get_answers(session_id=session["id"], demo=demo)
    with st.container(border=True, key="card_progress_chart"):
        st.plotly_chart(
            difficulty_chart(rows), width="stretch", key="progress_chart", config=CHART_CONFIG
        )
        st.caption(
            f"Seed сессии: {session['seed']}. Точки — фактическая сложность вопросов, "
            "пунктир — целевой уровень после каждого ответа."
        )

    st.download_button(
        "Скачать историю ответов (CSV)",
        data=db.answers_to_csv(db.get_answers(demo=demo), topics),
        file_name="adaptivelearn_demo.csv" if demo else "adaptivelearn_history.csv",
        mime="text/csv",
    )


# ---------- about tab ----------

ABOUT = """
#### Назначение
AdaptiveLearn — тренажёр по основам Python. Ученик отвечает на вопросы с выбором
ответа, а программа после каждого ответа подбирает сложность следующего вопроса.

**Аудитория:** начинающие изучать Python (школьники, студенты первых курсов).

#### Правила адаптации
1. Сессия начинается с целевого уровня 2 (средняя сложность).
2. Два верных ответа подряд повышают целевой уровень на 1.
3. Один неверный ответ понижает целевой уровень на 1.
4. Уровень всегда остаётся в пределах от 1 до 3.
5. Серия верных ответов обнуляется после ошибки или после смены уровня.
6. Следующий вопрос выбирается среди ещё не заданных вопросов целевого уровня.
   Если такие закончились, берётся ближайшая доступная сложность; при равном
   расстоянии — более лёгкая.
7. Фактическая сложность заданного вопроса хранится отдельно от целевого уровня.
8. Порядок вопросов определяется случайным числом (seed), которое сохраняется,
   поэтому выбор можно воспроизвести.

Система работает **по явным правилам**, а не на обученной модели искусственного
интеллекта. Каждое изменение сложности можно объяснить по правилам выше.
Алгоритм не измеряет уровень знаний и не гарантирует улучшения результатов обучения.

#### Технологии
Python, Streamlit (интерфейс), SQLite (хранение сессий и ответов),
Plotly (график), pytest (автоматические тесты). Работает локально, без интернета
и платных сервисов.

#### Практическая польза
- Ученик сразу видит объяснение и рекомендацию после каждого ответа.
- Сложность меняется по прозрачным правилам, которые легко проверить.
- История ответов сохраняется и выгружается в CSV для анализа.

#### Ограничения
- Небольшой банк: 45 вопросов, по 15 на тему и по 5 на каждый уровень сложности.
- Только вопросы с выбором ответа; написание кода не проверяется.
- Уровни сложности назначены автором вопросов экспертно, без статистической калибровки.
- Правила адаптации простые и не учитывают время ответа и повторение материала.
- Нет учётных записей: вся статистика относится к одному локальному пользователю.
- Эффективность для обучения не исследовалась.

#### Исследовательская гипотеза
«Подбор сложности по ответам ученика может помочь сделать учебную тренировку
более подходящей к его текущим результатам.»

Гипотеза пока не проверялась на учениках. Автоматические тесты подтверждают только то,
что алгоритм работает по описанным правилам. Проверка учебной эффективности требует
отдельного исследования с участниками.

#### Демонстрация на конференции
Отметьте флажок «Демонстрационная сессия» — такие сессии хранятся отдельно и
не попадают в статистику ученика.
1. Начните тренировку (первый вопрос — средней сложности, целевой уровень 2).
2. Ответьте верно на два вопроса подряд.
3. Покажите, что целевой уровень вырос до 3 и следующий вопрос — сложный.
4. Ответьте неверно.
5. Покажите, что целевой уровень снизился до 2.
6. Завершите сессию и разберите объяснения ошибок и график
   «Изменение сложности заданий».
"""


def render_about() -> None:
    with st.container(border=True, key="card_about"):
        st.markdown(ABOUT)


# ---------- layout ----------

def main() -> None:
    init_storage()
    bank = load_bank()

    st.title("AdaptiveLearn")
    st.caption("Тренажёр Python с адаптивной сложностью")

    tab_train, tab_progress, tab_about = st.tabs(["Тренировка", "Прогресс", "О проекте"])
    with tab_train:
        render_training(bank)
    with tab_progress:
        render_progress(bank)
    with tab_about:
        render_about()


main()
