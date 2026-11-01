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
- Сделано: все **10 шагов** кабинета в `master`. Идёт `feature/prod-ready-llm`: Gemini primary, Ollama secondary, quality-gate фактов редактора, тон README.
- Контур: stand `:8100`, api `:8000`, actions `:8220`, PDP `:9100`, guardrail `:8300`, Postgres `:5434`, Ollama `:11434`.
- Primary: Gemini `gemini-3-flash-preview` (ключ только в локальном `.env`). Secondary: Ollama `qwen2.5:7b`. llama-server на этой машине не стартует (Smart App Control / WinError 4551). Free-tier Gemini при 429 уходит на secondary.
- Пул: `app/llm_pool.py` + `set_default_openai_client`; агенты не пересобираются.

### Что доказано измерением

- `smoke` **97/97** (2026-11-01, `feature/prod-ready-llm`; G=7 = 43, в т.ч. `facts_gate_*` и reinforce редактора).
- `scenarios` после смены primary: **11/11** (2026-11-01; при 429 Gemini ход шёл через secondary Ollama — в `llm_calls` видно `provider=secondary`). Ранее на Ollama как primary: 11/11 ×8 из 9.
- Чистая копия на пустой базе (ранее, до gate фактов): `make setup` + пять процессов + `make smoke` **92/92**. Linux/macOS не проверялись.
- Сценарий 20 до правки падал почти всегда: агент данных после tool-call отдавал пустой/мусорный доклад (`mistral`, `llama3.2:3b`, `qwen3-vl:8b` не лучше).
- Правка: редактору — сырой вывод инструментов data, в промпте data — `list_my_charges`. Цепочка data→kb→editor: «149» в ответе 12/12.
- Повтор: если агент `tariff`/`tickets` не вызвал ни одного write-инструмента — один повтор хода. Вызвал и получил отказ — не повторяем. Барьеры те же.
- Найдено и исправлено полной проверкой: `route()` принимал «Роуминг у меня включён?» за команду и слал в `tariff`; «из базы знаний» не вело в `kb`; regex имени тарифа ломал слова («неигровой»).
- Живой failover в обе стороны доказан: раньше primary Qwen → secondary Gemini; сейчас primary Gemini при 429 → secondary Qwen (чат «почему списание» ответил с суммой 440).
- Quality-gate: если в `data_facts` есть суммы/тарифы, а ответ редактора их не содержит — один повтор с reinforce.
- Шаг 9 (ранее): scenarios 11/11; trace executed/duplicate/inject/degraded — в [`acceptance-report.md`](acceptance-report.md).

### Ждёт решения

_(пусто)_

### Решения (зафиксировано)

- Автор коммитов — Space108.
- Secondary model: `gemini-3-flash-preview` (gemini-2.0/2.5-flash недоступны этому ключу).

---

## Краткая история (свежее)

| Дата | Что |
|------|-----|
| 2026-11-01 | `feature/prod-ready-llm`: Gemini primary, fact-gate редактора; smoke 97/97; scenarios 11/11. |
| 2026-11-01 | `feature/readme` → `master` (`ce5dbd9`); smoke 92/92; hexlet-check и ci на merge — success. |
| 2026-11-01 | Шаг 10: llm_pool + Gemini secondary; failover и возврат primary доказаны; smoke 72/72. |
| 2026-11-01 | Шаг 9 в `master` (`77a4579`); smoke 64/64; фикс olga overview. |
| 2026-11-01 | Чистка репо `1c0636f`; шаг 8 scenarios 11/11 ×5. |

Старше — `docs/STATUS_ARCHIVE.md`.
