"""Редактор: собирает финальный ответ абоненту из докладов агентов.

Единственный агент, чей текст уходит пользователю.

TODO(шаг 1):
  1. EDITOR_PROMPT: кратко, по-русски, только на фактах докладов;
     источник из базы знаний — обязательно (источник: <source>);
     ничего не выдумывать; без докладов — ответить самому.
  2. build_editor(session) — агент без инструментов.
  3. editor_task(message, runs) — склейка: сообщение абонента +
     нумерованные доклады агентов + просьба написать ответ.
"""
from __future__ import annotations

from app import llm

EDITOR_PROMPT = """Ты редактор ответов AI-оператора мобильной связи.
Пиши кратко, по-русски, только на фактах из докладов агентов.
Если в докладах есть статьи базы знаний — обязательно назови источник
в виде: источник: <source>.
Ничего не выдумывай. Если докладов нет — ответь сам по сообщению абонента,
но не выдумывай факты об абоненте и тарифах.
"""


def build_editor(session):
    del session  # сессия понадобится на следующих шагах
    return llm.build_agent("editor", EDITOR_PROMPT, tools=[])


def editor_task(message: str, runs: list[dict]) -> str:
    parts = [f"Сообщение абонента:\n{message}", "", "Доклады агентов:"]
    if not runs:
        parts.append("(нет докладов)")
    else:
        for i, run in enumerate(runs, 1):
            agent = run.get("agent", "?")
            report = run.get("report", "")
            parts.append(f"{i}. [{agent}]\n{report}")
    parts.append("")
    parts.append("Напиши финальный ответ абоненту.")
    return "\n".join(parts)
