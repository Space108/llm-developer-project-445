# AI-оператор мобильного оператора — текущий шаг

## Как вести STATUS

- Только **факты** после прогона / выкладки / проверки. Без рецептов и команд.
- «Сейчас» короткий (файл ≤ 120 строк, строка ≤ 400 знаков). Устаревшее и длинные абзацы — в архив слово в слово.
- Противоречие дат → верить более поздней.
- Противоречие со старым текстом в архиве → верить этому файлу.
- Здесь живёт **стек и состояние** проекта (§ «Продукт»). В других файлах их не дублировать.
- Неизвестное пишем явно: **«не знаю»** + что получить.

---

## Сейчас (на 2026-11-01)

### Продукт

- Хекслет «AI-оператор…», **10 шагов**. Канон — **номер шага в кабинете**, не путать с `G=` / TODO в коде.
- Сделано по кабинету: … / шаг 7 guardrail / шаг 8 сценарии / **шаг 9 приёмка в работе** (`feature/acceptance`).
- Контур: stand `:8100`, api `:8000`, actions `:8220`, PDP `:9100`, guardrail `:8300`, Postgres `:5434`, llama-server `:8081`.
- LLM: `Qwen2.5-7B-Instruct-Q4_K_M.gguf`; Serve: `D:\llama\start-server.ps1` (`--parallel 1`, `--ctx-size 4096`).
- База `master` = `1c0636f`. Работа в `feature/acceptance`. Автор Space108. Python 3.11.9 `.venv`.

### Что доказано измерением

- Приёмка на чистом стенде (migrate+seed+reset): **`smoke` 64/64**, **`scenarios` 11/11**.
- Живой чек-лист: anna карточка→«да»→`executed` (`tr_5182afaa002a`) + повтор «да»→`duplicate_suppressed` (`tr_46505b279d5f`); boris без карточки; olga обзор `u_4f2a` без prepare.
- Inject → `guardrail_unsafe` (`tr_1d13345dd5d8`); down→`guardrail_degraded` (`tr_aefe7292ae95`) без карточки записи; up→карточка снова.
- 401 без Bearer с `resource_metadata`; чужой aud → 401; `policy_events` содержат `no_write_scope` / `session_limit` / `guard_not_ok`.
- Resilience: actions kill/up → следующий ход с карточкой; pdp restart → старая карточка не исполняет, новая сессия ок.
- Починка olga: `get_subscriber_overview` в data + grounding `subscriber_id`; журнал `tool_calls` args `u_4f2a`; smoke G=7 +2 проверки.

### Документация / смок

- Отчёт шага 9: [`acceptance-report.md`](acceptance-report.md). ADR: [`decisions.md`](decisions.md). CI: `.github/workflows/ci.yml` (smoke G=7).

### Ждёт решения

- Коммит / push / merge `feature/acceptance`.
- Текст кабинета **шаг 10**.

### Решения (зафиксировано)

- Автор коммитов — Space108.
- Python 3.11 ок. Инференс: llama-server + Qwen2.5-7B (не SGLang).
- Принято: кабинет шаг 5–9 (приёмка) и усиления отчёт/ADR/CI.

---

## Краткая история (свежее)

| Дата | Что |
|------|-----|
| 2026-11-01 | Шаг 9 приёмка в `feature/acceptance`: чек-лист + фикс olga overview; smoke 64/64, scenarios 11/11. |
| 2026-11-01 | Чистка репо в `master` (`1c0636f`): без `.cursor`/`check_docs` в индексе. |
| 2026-11-01 | Шаг 8 + ADR/CI в `master`; scenarios 11/11 ×5; smoke 62/62. |
| 2026-11-01 | Шаг 7 guardrail; G=5→9/9; живой inject/down/up. |
| 2026-11-01 | Шаг 6 PDP; G=3+G=4 зелёные. |
| 2026-11-01 | Шаг 5 права/JWT; живой Борис. |
| 2026-10-31 | Аудит; живой эталон; G=0…2. |

Старше — `docs/STATUS_ARCHIVE.md` (поиск по дате).
