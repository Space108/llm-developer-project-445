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

- Хекслет «AI-оператор…», **10 шагов**. Канон — номер шага в кабинете ≠ `G=`.
- Сделано: все **10 шагов** кабинета в `master` (`5c1ab25`): шаг 9 приёмка, шаг 10 LLM pool. Идёт README (`feature/readme`).
- Контур: stand `:8100`, api `:8000`, actions `:8220`, PDP `:9100`, guardrail `:8300`, Postgres `:5434`, llama `:8081`.
- Primary: Qwen2.5-7B на `:8081`. Secondary: Gemini OpenAI-compat (`gemini-3-flash-preview`), ключ только в локальном `.env`.
- Пул: `app/llm_pool.py` + `set_default_openai_client`; агенты не пересобираются.

### Что доказано измерением

- `smoke` **72/72** (G=7: политика пула — 400 без failover, 5xx→cooldown+switch).
- Живой failover: `LLM_BASE_URL` → `:9` → «какой у меня тариф?» отвечает; `llm_calls` `provider=secondary`, model `gemini-3-flash-preview`, `cost_rub` по цене secondary.
- Primary восстановлен: тот же вопрос → `provider=primary`, model Qwen.
- Шаг 9 (ранее): scenarios 11/11; trace executed/duplicate/inject/degraded — в [`acceptance-report.md`](acceptance-report.md).

### Ждёт решения

- Коммит / push / merge `feature/readme` (быстрый старт; все 10 шагов кабинета в `master` `5c1ab25`).

### Решения (зафиксировано)

- Автор коммитов — Space108.
- Secondary model: `gemini-3-flash-preview` (gemini-2.0/2.5-flash недоступны этому ключу).

---

## Краткая история (свежее)

| Дата | Что |
|------|-----|
| 2026-11-01 | Шаг 10: llm_pool + Gemini secondary; failover и возврат primary доказаны; smoke 72/72. |
| 2026-11-01 | Шаг 9 в `master` (`77a4579`); smoke 64/64; фикс olga overview. |
| 2026-11-01 | Чистка репо `1c0636f`; шаг 8 scenarios 11/11 ×5. |

Старше — `docs/STATUS_ARCHIVE.md`.
