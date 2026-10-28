#!/usr/bin/env python3
"""Проверка документации агента по стандарту (канон: правила + STATUS + ROADMAP).

Что проверяет:
  1. Обязательные файлы канона существуют.
  2. В канонических файлах не осталось незаполненных {{плейсхолдеров}} шаблона.
  3. Core содержит жёсткие правила (core_must_contain): ветки, смоки, «явной команде», «не знаю»...
  4. Core не держит состояние проекта: даты, IP и т.п. (core_forbidden_regex) — факты живут в STATUS.
  5. Ссылки в канонических файлах ведут на существующие файлы:
       - `docs/x.md`, [..](x.md) — ошибка, если файла нет;
       - голое имя `x.py` / `x.json` — ищется по всему репо (по имени); не нашли — только ИНФО
         (это может быть рантайм-файл завода; режим bare_names: off | info | warn | error);
       - абсолютные пути (/data/x.json, C:/...) и блоки кода ``` ``` — не проверяются.
  6. Устаревшие термины (stale_terms) не звучат как «актуально» (нужна пометка «выкл / снят / история»).
  7. Размеры: строки (max_lines), байты (max_bytes) и ДЛИННЫЕ СТРОКИ (max_line_chars) —
     абзац на 2000 знаков в одной строке больше не обходит лимит.
  8. ROADMAP: ✅ внутри «Далее» без пометки «в работе» — готовое уходит в «Сделано».
     В работе — 🔄 + «в работе» (не зелёная ✅).
  9. ROADMAP «Актуализация» не отстаёт от даты «Сейчас» в STATUS больше чем на max_date_gap_days.
 10. Таблица «ОБЯЗАТЕЛЬНОЕ ОЗНАКОМЛЕНИЕ» в STATUS: первой строкой идёт файл core (по имени из core_file), а не STATUS.
 11. Даты «Сейчас» (STATUS) и «Актуализация» (ROADMAP) не в будущем.

Запуск:  python scripts/check_docs.py                  # из корня проекта
         python scripts/check_docs.py --strict         # предупреждения тоже дают код 1
         python scripts/check_docs.py --root X --config Y.json   # чужой репо, конфиг снаружи
Настройки: .doc-standard.json в корне проекта (читается и с BOM — файл из PowerShell не ломает).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ENC = "utf-8-sig"  # BOM (PowerShell, Блокнот) не ломает чтение

LINK_RE = re.compile(
    r"`([A-Za-z0-9_.\-/\\]+\.(?:md|mdc|json|py|sh|yml|yaml|toml|txt))`|\]\(([^)#\s]+\.(?:md|mdc))\)"
)
GLOB_CHARS = set("*<>{}$")
DOC_EXT = {".md", ".mdc"}
PLACEHOLDER_RE = re.compile(r"\{\{(?!\.)[^{}\n]+\}\}")  # {{.Names}} — формат docker, не плейсхолдер
FENCE_RE = re.compile(r"^\s*(```|~~~)")
DATE_RE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{2}\.\d{4})"

DEFAULTS: dict = {
    "required": [],
    "canon": [],
    "core_file": "",
    "core_must_contain": [],
    "core_forbidden_regex": [],
    "status_file": "docs/STATUS.md",
    "roadmap_file": "docs/ROADMAP.md",
    "max_lines": {},
    "max_bytes": {},
    "max_line_chars": 0,
    "max_date_gap_days": 3,
    "bare_names": "info",
    "stale_terms": [],
    "ok_markers": [],
    "ignore_paths": [],
    "index_skip_dirs": [],
}

SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", "env", "dist", "build", "site-packages",
    "target", "out", "coverage", "htmlcov",
}
KEEP_DOT_DIRS = {".cursor", ".github"}


def read(path: Path) -> str:
    return path.read_text(encoding=ENC)


def strip_fences(text: str) -> str:
    """Заменяет строки внутри ``` блоков на пустые (номера строк сохраняются)."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def load_config(root: Path, cfg_arg: str | None) -> dict:
    cfg_path = Path(cfg_arg) if cfg_arg else root / ".doc-standard.json"
    if not cfg_path.exists():
        sys.exit(f"нет {cfg_path.name} в {cfg_path.parent}")
    cfg = json.loads(read(cfg_path))
    merged = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    merged.update(cfg)
    return merged


# ---------- индекс файлов репо (для ссылок по голому имени) ----------

def build_index(root: Path, cfg: dict) -> set[str]:
    skip = SKIP_DIRS | set(cfg.get("index_skip_dirs", []))
    names: set[str] = set()
    for cur, dirs, files in os.walk(root):
        dirs[:] = [
            d for d in dirs
            if d not in skip and not d.lower().startswith(("venv", ".venv")) and (not d.startswith(".") or d in KEEP_DOT_DIRS)
        ]
        names.update(files)
    return names


# ---------- проверки ----------

def check_required(root: Path, cfg: dict) -> list[str]:
    return [f"ОШИБКА: нет обязательного файла {rel}" for rel in cfg["required"] if not (root / rel).exists()]


def check_placeholders(root: Path, cfg: dict) -> list[str]:
    errors: list[str] = []
    for rel in cfg["canon"]:
        path = root / rel
        if not path.exists():
            continue
        for n, line in enumerate(read(path).splitlines(), 1):
            m = PLACEHOLDER_RE.search(line)
            if m:
                errors.append(f"ОШИБКА: {rel}:{n} не заполнен плейсхолдер {m.group(0)}")
    return errors


def check_core_rules(root: Path, cfg: dict) -> list[str]:
    core, needed = cfg["core_file"], cfg["core_must_contain"]
    if not core or not needed or not (root / core).exists():
        return []
    text = read(root / core)
    return [f"ОШИБКА: в {core} нет обязательного правила «{phrase}»" for phrase in needed if phrase not in text]


def check_core_no_state(root: Path, cfg: dict) -> list[str]:
    """Один факт — одно место: core = поведение, состояние проекта (даты, IP) живёт в STATUS."""
    core, patterns = cfg["core_file"], cfg["core_forbidden_regex"]
    if not core or not patterns or not (root / core).exists():
        return []
    errors: list[str] = []
    for n, line in enumerate(read(root / core).splitlines(), 1):
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                errors.append(f"ОШИБКА: {core}:{n} состояние проекта в core («{m.group(0)}») — перенеси в STATUS")
    return errors


def check_links(root: Path, cfg: dict) -> tuple[list[str], list[str], list[str]]:
    """Возвращает (ошибки, предупреждения, инфо)."""
    errors: list[str] = []
    warns: list[str] = []
    infos: list[str] = []
    ignore = set(cfg["ignore_paths"])
    mode = cfg["bare_names"]
    index: set[str] | None = None
    for rel in cfg["canon"]:
        path = root / rel
        if not path.exists():
            continue
        text = strip_fences(read(path))
        for m in LINK_RE.finditer(text):
            ref = (m.group(1) or m.group(2)).replace("\\", "/")
            if any(ch in ref for ch in GLOB_CHARS) or ref in ignore:
                continue
            if ref.startswith("/") or re.match(r"^[A-Za-z]:/", ref):
                continue  # абсолютный путь (рантайм завода, чужая машина) — не проверяем
            if (root / ref).exists() or (path.parent / ref).exists():
                continue
            line = text.count("\n", 0, m.start()) + 1
            if "/" in ref:
                errors.append(f"ОШИБКА: {rel}:{line} ссылка на несуществующий файл `{ref}`")
                continue
            if index is None:
                index = build_index(root, cfg)
            if ref in index:
                continue
            ext = Path(ref).suffix.lower()
            msg = f"{rel}:{line} нет файла `{ref}` ни в корне, ни где-либо в репо"
            if ext in DOC_EXT:
                errors.append(f"ОШИБКА: {msg}")
            elif mode == "error":
                errors.append(f"ОШИБКА: {msg}")
            elif mode == "warn":
                warns.append(f"ПРЕДУПРЕЖДЕНИЕ: {msg}")
            elif mode == "info":
                infos.append(f"ИНФО: {msg} (рантайм-файл? для тишины — ignore_paths)")
    return errors, warns, infos


def _marker_regexes(markers: list[str]) -> list[re.Pattern]:
    out = []
    for mk in markers:
        mk = mk.lower()
        if not mk:
            continue
        left = r"(?<!\w)" if mk[0].isalnum() else ""
        right = r"(?!\w)" if (len(mk) <= 3 and mk[-1].isalnum()) else ""  # короткие («off») — целым словом
        out.append(re.compile(left + re.escape(mk) + right))
    return out


def check_stale(root: Path, cfg: dict) -> list[str]:
    warns: list[str] = []
    terms = cfg["stale_terms"]
    markers = _marker_regexes(cfg["ok_markers"])
    for rel in cfg["canon"]:
        path = root / rel
        if not path.exists():
            continue
        for n, line in enumerate(read(path).splitlines(), 1):
            low = line.lower()
            for term in terms:
                if term.lower() in low and not any(rx.search(low) for rx in markers):
                    warns.append(f"ПРЕДУПРЕЖДЕНИЕ: {rel}:{n} «{term}» без пометки «выкл/снят/история»: {line.strip()[:110]}")
    return warns


def check_sizes(root: Path, cfg: dict) -> list[str]:
    warns: list[str] = []
    line_limits = dict(cfg["max_lines"])
    byte_limits = dict(cfg["max_bytes"])
    long_limit = int(cfg.get("max_line_chars") or 0)
    for rel, limit in line_limits.items():
        path = root / rel
        if path.exists():
            n = len(read(path).splitlines())
            if n > int(limit):
                warns.append(f"ПРЕДУПРЕЖДЕНИЕ: {rel} раздут: {n} строк > {limit} — вынеси лишнее (процедуры в safe-dev, история в архив)")
    for rel, limit in byte_limits.items():
        path = root / rel
        if path.exists():
            size = path.stat().st_size
            if size > int(limit):
                warns.append(f"ПРЕДУПРЕЖДЕНИЕ: {rel} тяжёлый: {size} байт > {limit} — длинные абзацы в архив или ROADMAP")
    if long_limit:
        for rel in sorted(set(line_limits) | set(byte_limits)):
            path = root / rel
            if not path.exists():
                continue
            lines = read(path).splitlines()
            long_idx = [(len(s), i) for i, s in enumerate(lines, 1) if len(s) > long_limit]
            if long_idx:
                longest, at = max(long_idx)
                warns.append(
                    f"ПРЕДУПРЕЖДЕНИЕ: {rel}: {len(long_idx)} строк длиннее {long_limit} знаков "
                    f"(длиннейшая {longest}, строка {at}) — разбей на пункты или перенеси в архив"
                )
    return warns


def check_roadmap_done_in_next(root: Path, cfg: dict) -> list[str]:
    rel = cfg["roadmap_file"]
    path = root / rel
    if not path.exists():
        return []
    warns: list[str] = []
    in_next = False
    for n, line in enumerate(strip_fences(read(path)).splitlines(), 1):
        if line.startswith("## "):
            in_next = "далее" in line.lower()
            continue
        if in_next and "✅" in line and "в работе" not in line.lower():
            warns.append(f"ПРЕДУПРЕЖДЕНИЕ: {rel}:{n} ✅ в «Далее» без «в работе» — готовое уходит в «Сделано» (в работе — 🔄): {line.strip()[:90]}")
        if in_next and "🔄" in line and "в работе" not in line.lower():
            warns.append(f"ПРЕДУПРЕЖДЕНИЕ: {rel}:{n} 🔄 в «Далее» без подписи «в работе»: {line.strip()[:90]}")
    return warns


def _parse_date(s: str) -> date | None:
    try:
        if "-" in s:
            y, m, d = (int(x) for x in s.split("-"))
        else:
            d, m, y = (int(x) for x in s.split("."))
        return date(y, m, d)
    except ValueError:
        return None


def check_dates(root: Path, cfg: dict) -> list[str]:
    status_p, road_p = root / cfg["status_file"], root / cfg["roadmap_file"]
    if not status_p.exists() or not road_p.exists():
        return []
    ms = re.search(r"##\s*Сейчас[^\n]*?" + DATE_RE, read(status_p))
    mr = re.search(r"Актуализация[^\n]*?" + DATE_RE, read(road_p))
    if not ms or not mr:
        return []
    ds, dr = _parse_date(ms.group(1)), _parse_date(mr.group(1))
    if not ds or not dr:
        return []
    today = date.today()
    for label, d, rel in (("STATUS «Сейчас»", ds, cfg["status_file"]), ("ROADMAP «Актуализация»", dr, cfg["roadmap_file"])):
        if (d - today).days > 1:
            return [f"ПРЕДУПРЕЖДЕНИЕ: {rel}: дата {label} {d.isoformat()} в будущем (сегодня {today.isoformat()}) — поправь дату"]
    gap = (ds - dr).days
    if gap > int(cfg["max_date_gap_days"]):
        return [
            f"ПРЕДУПРЕЖДЕНИЕ: {cfg['roadmap_file']} «Актуализация» {dr.isoformat()} отстаёт от «Сейчас» "
            f"в {cfg['status_file']} ({ds.isoformat()}) на {gap} дн. — обнови дату или содержимое"
        ]
    return []


def check_reading_order(root: Path, cfg: dict) -> list[str]:
    rel = cfg["status_file"]
    path = root / rel
    if not path.exists():
        return []
    lines = read(path).splitlines()
    start = next((i for i, s in enumerate(lines) if "ОЗНАКОМЛЕНИЕ" in s.upper() and s.lstrip().startswith("#")), None)
    if start is None:
        return []
    rows = []
    for s in lines[start + 1:]:
        if s.startswith("#") and rows:
            break
        if s.lstrip().startswith("|"):
            rows.append(s)
    data = [r for r in rows if not re.match(r"^\s*\|[\s:\-|]+\|\s*$", r)]
    if len(data) < 2:
        return []
    first = data[1].lower()  # data[0] — заголовок таблицы
    core_name = Path(cfg["core_file"]).name.lower() if cfg.get("core_file") else "core"
    if core_name not in first:  # имя файла core целиком: slug со словом «core» (my-core, core-api) не обманет
        return [f"ПРЕДУПРЕЖДЕНИЕ: {rel}: в таблице ознакомления первой идёт не core («{data[1].strip()[:80]}») — порядок: core → safe-dev → STATUS → ROADMAP"]
    return []


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".", help="корень проекта")
    ap.add_argument("--config", default=None, help="путь к конфигу (по умолчанию <root>/.doc-standard.json)")
    ap.add_argument("--strict", action="store_true", help="предупреждения тоже дают код 1")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    cfg = load_config(root, a.config)

    link_err, link_warn, link_info = check_links(root, cfg)
    errors = (
        check_required(root, cfg)
        + check_placeholders(root, cfg)
        + check_core_rules(root, cfg)
        + check_core_no_state(root, cfg)
        + link_err
    )
    warns = (
        link_warn
        + check_stale(root, cfg)
        + check_sizes(root, cfg)
        + check_roadmap_done_in_next(root, cfg)
        + check_dates(root, cfg)
        + check_reading_order(root, cfg)
    )

    for line in errors + warns + link_info:
        print(line)
    print(
        f"\nканон: {len(cfg['canon'])} файлов; ошибок: {len(errors)}; "
        f"предупреждений: {len(warns)}; инфо: {len(link_info)}"
    )
    if errors or (a.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
