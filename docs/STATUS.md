# AI-оператор мобильного оператора — текущий шаг

## Как вести STATUS

- Только **факты** после прогона / выкладки / проверки. Без рецептов и команд.
- «Сейчас» короткий (файл ≤ 120 строк, строка ≤ 400 знаков). Устаревшее и длинные абзацы — в архив слово в слово.
- Противоречие дат → верить более поздней.
- Противоречие со старым текстом в архиве → верить этому файлу.
- Здесь живёт **стек и состояние** проекта (§ «Продукт»). В других файлах их не дублировать.
- Неизвестное пишем явно: **«не знаю»** + что получить.

---

## Сейчас (на 2026-11-13)

### Списания: даты и «куда уходят деньги»

- Сид обрывался на сентябре → бот выдумывал «1 октября» при сегодняшнем 13.11.2026. В `demo.yaml` добавлены октябрь/ноябрь; в `list_my_charges`/`get_my_profile` — `today` и `next_charge_date` (1-е следующего месяца). Промпты data/kb/editor — не называть прошлое «следующим» списанием.
- Живой прогон 2026-11-13: «Когда спишут…» → **1 декабря 2026**; история окт/ноя без подмены будущего. Сценарий 20 зелёный; smoke G=7 **53/53**.

### Ревью наставника — `feature/mentor-review` (не слито в `master`)

- Исполнение карточки — только из `pending` после Allow PEP; успех → `confirmed`, сбой → `failed`; повторное «да» — ответ из `idempotency` без MCP. Кнопка — через `decide_card` (свой trace_id, guardrail, `rejected_by_user`). Отмена → ответ «Отменено».
- `classify_answer` — целое слово / короткая реплика; grounding имени — только при одном имени; маска контактов одна для `audit_log`, `tool_calls` и `idempotency`; планировщик пишет `llm_calls`; пул без глобального клиента (`RunConfig` на вызов).
- Контакты не лежат открытым текстом в журналах (одно правило `audit.redact_args`). `wrap.drop_nulls` — optional `null` не ломает MCP. Календарь стенда: `today` / `next_charge_date` в профиле и списаниях.
- Проверено 2026-11-13: `smoke` **115/115**; `scenarios` **12/12** (в т.ч. `38_question_after_change`); `check_docs --strict` чисто. В README — два скрина чата (`docs/screens/`).
- Сбой исполнения вживую (`scripts/probe_actions_down.py`): `actions` вниз → «да» → карточка `failed`, тариф не меняется; после подъёма вопросы и «да» не дают `executed`.
- Параллельный пул: фейковая гонка **40/40**; живой failover **12/12**, `journal_mismatch=0`; 8 чатов **8/8**. Скрипты: `scripts/probe_pool_parallel.py`, `scripts/probe_chat_parallel.py`.

### Продукт

- Хекслет «AI-оператор…», **10 шагов**. Канон — номер шага в кабинете ≠ `G=`.
- Сделано: все **10 шагов** кабинета + боевой профиль LLM в `master` (`1b2b51f` / `3b7d21d`, merge `feature/prod-ready-llm`): Gemini primary, Ollama secondary, quality-gate фактов редактора.
- **Автопроверка Хекслета пройдена** (2026-11-02): кабинет показал «АВТОПРОВЕРКА ПРОЙДЕНА» на текущем `master`.
- Контур: stand `:8100`, api `:8000`, actions `:8220`, PDP `:9100`, guardrail `:8300`, Postgres `:5434`, Ollama `:11434`.
- Primary: Gemini `gemini-3-flash-preview` (ключ только в локальном `.env`). Secondary: Ollama `qwen2.5:7b`. llama-server на этой машине не стартует (Smart App Control / WinError 4551). Free-tier Gemini при 429 уходит на secondary.
- Пул: `app/llm_pool.py` через `RunConfig` на вызов; агенты не пересобираются.

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
| 2026-11-13 | `feature/mentor-review`: правки по ревью; smoke 115/115; scenarios 12/12; скрины в README. |
| 2026-11-02 | Автопроверка Хекслета пройдена на `master`. |
| 2026-11-01 | `feature/prod-ready-llm`: Gemini primary, fact-gate редактора; smoke 97/97; scenarios 11/11. |
| 2026-11-01 | `feature/readme` → `master` (`ce5dbd9`); smoke 92/92; hexlet-check и ci на merge — success. |
| 2026-11-01 | Шаг 10: llm_pool + Gemini secondary; failover и возврат primary доказаны; smoke 72/72. |
| 2026-11-01 | Шаг 9 в `master` (`77a4579`); smoke 64/64; фикс olga overview. |
| 2026-11-01 | Чистка репо `1c0636f`; шаг 8 scenarios 11/11 ×5. |

Старше — `docs/STATUS_ARCHIVE.md`.
