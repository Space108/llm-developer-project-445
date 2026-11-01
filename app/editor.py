"""Редактор: собирает финальный ответ абоненту из докладов агентов.

Единственный агент, чей текст уходит пользователю.
"""
from __future__ import annotations

import re

from app import llm

EDITOR_PROMPT = """Ты редактор ответов AI-оператора мобильной связи.
Пиши кратко, только на русском языке, только на фактах из докладов агентов.
Запрещено: китайский, английский и любой другой язык; дублировать ответ переводом.
Если в докладах есть статьи базы знаний — в конце одной строкой укажи
человекочитаемый источник, например: «По правилам списаний» или
«По условиям тарифа «Базовый»». Не пиши технические id вроде charge_rules.
Ничего не выдумывай. Если докладов нет — ответь сам по сообщению абонента,
но не выдумывай факты об абоненте и тарифах.
Не пиши, что тикет создан или тариф сменён, если этого нет в докладах.
"""

# CJK и сходные блоки — Qwen иногда дописывает китайский «перевод» после русского.
_NON_RU_BLOCK = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af"
    r"\u3000-\u303f\uff00-\uffef]+"
)

# технические id статей → заголовки для абонента
_SOURCE_TITLES = {
    "charge_rules": "правила списаний",
    "change_rules": "правила смены тарифа",
    "roaming": "правила роуминга",
    "services_rules": "правила услуг",
    "faq_contacts": "часто задаваемые вопросы",
    "tariff_bazovy": "тариф «Базовый»",
    "tariff_igrovoy": "тариф «Игровой»",
    "tariff_semeyny": "тариф «Семейный»",
    "tariff_pro": "тариф «Про»",
}
_SOURCE_LINE = re.compile(
    r"(?i)(источник|источники)\s*:\s*([^\n.]+)",
)


def humanize_sources(text: str) -> str:
    """Заменить charge_rules и т.п. на нормальные названия статей."""

    def _one(match: re.Match[str]) -> str:
        raw = match.group(2)
        parts = [p.strip(" «»\"'") for p in re.split(r"[,;|/]", raw) if p.strip()]
        titles: list[str] = []
        for part in parts:
            key = part.lower().replace(".md", "").strip()
            titles.append(_SOURCE_TITLES.get(key, part))
        # одна ссылка — «По …»; несколько — через запятую
        if len(titles) == 1:
            return f"По материалам: {titles[0]}"
        return f"По материалам: {', '.join(titles)}"

    return _SOURCE_LINE.sub(_one, text or "")


# Модель мешает кириллицу и латиницу: «Иггovой», «Игrovoy», «igrovoy».
_TARIFF_FIXES = (
    # игровой: И/I + г/g (1–2) + опц. р/r + о/o + в/v + о/o + й/y
    (
        re.compile(
            r"[ИиIi][гГgG]{1,2}[рРrR]?[оОoO][вВvV][оОoO][йЙyYjJ]",
        ),
        "Игровой",
    ),
    # семейный / базовый — только явная латиница или смешанный скрипт
    (re.compile(r"(?i)\bsemeyn\w*\b"), "Семейный"),
    (re.compile(r"(?i)\bсемeyn\w*\b"), "Семейный"),
    (re.compile(r"(?i)\bbazov\w*\b"), "Базовый"),
    (re.compile(r"(?i)\bбазовyy\b|\bбазov\w*\b"), "Базовый"),
)


def fix_tariff_names(text: str) -> str:
    out = text or ""
    for pattern, repl in _TARIFF_FIXES:
        out = pattern.sub(repl, out)
    out = re.sub(r"(?i)тариф\s*[«\"]?pro[»\"]?", "тариф «Про»", out)
    return out


def sanitize_reply(text: str) -> str:
    """Убрать иноязычные вставки, тех. id источников и кривые имена тарифов."""
    raw = fix_tariff_names(humanize_sources((text or "").strip()))
    if not raw:
        return raw
    if not _NON_RU_BLOCK.search(raw):
        return raw
    cleaned = _NON_RU_BLOCK.sub("", raw)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip(" \t\n·—-")
    if len(re.sub(r"\s+", "", cleaned)) < 8:
        return "Не удалось сформировать ответ на русском. Переформулируйте, пожалуйста."
    return cleaned


def build_editor(session):
    del session  # сессия понадобится на следующих шагах
    from agents import ModelSettings

    return llm.build_agent(
        "editor",
        EDITOR_PROMPT,
        tools=[],
        model_settings=ModelSettings(max_tokens=320),
    )


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
    parts.append(
        "Напиши финальный ответ абоненту строго на русском, без перевода на другие языки."
    )
    return "\n".join(parts)
