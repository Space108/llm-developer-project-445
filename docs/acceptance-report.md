# Отчёт приёмки (шаг 9 кабинета)

Дата записи: 2026-11-01.  
Ветка: `feature/acceptance`.  
Окружение: локальный стенд Windows — Postgres `:5434`, stand `:8100`, api `:8000`, actions `:8220`, PDP `:9100`, guardrail `:8300`, llama-server `:8081` (`Qwen2.5-7B-Instruct-Q4_K_M.gguf`, `--parallel 1`, `--ctx-size 4096`).  
Прод и облачный деплой: нет.

Канон «где мы сейчас» — [`STATUS.md`](STATUS.md). Этот файл — что прогоняли, что нашли, что починили, со `trace_id`.

## Что прогоняли

| Блок | Команда / прогон | Итог |
|------|------------------|------|
| Стенд с нуля | `migrate` + `seed` + `POST /mock/admin/reset` + `smoke G=0` | 9/9 |
| Весь smoke | `make smoke` (G=0…5 + G=7) | **62/62**, после фикса — снова **64/64** (см. ниже) |
| Сценарии | `make scenarios` | **11/11** |
| Живой чек-лист | API-чат anna / boris / olga + auth + inject + down/up + resilience | закрыт (ниже) |

Между блоками — `make reset` (`POST /mock/admin/reset`).

## Живой чек-лист

### Веб/API-чат

| Кто | Что | Итог | Доказательство |
|-----|-----|------|----------------|
| anna | смена тарифа через карточку + «да» | тариф Базовый→Семейный, «Готово» | `trace_id=tr_5182afaa002a`, audit `executed`, before/after |
| anna | повторное «да» | одна операция в моках | `trace_id=tr_46505b279d5f`, audit `duplicate_suppressed` |
| boris | смена тарифа | без карточки, тариф Игровой | живой чат + мок `u_9b1c` |
| olga | обзор абонента `u_4f2a` без записи | имя Анна, тариф Базовый; prepare_* нет | `tool_calls`: `get_subscriber_overview` args `u_4f2a` |

Цепочка успешной смены (`tr_5182afaa002a`): plan/llm (`tariff`) → tool `prepare_tariff_change` → `policy_events` Prepare/ConfirmCard/Execute Allow → audit `executed` (before=Базовый, after=Семейный).

### Права и безопасность

| Пункт | Итог |
|-------|------|
| Без Bearer → 401 + `resource_metadata` в `WWW-Authenticate` | да (`/.well-known/oauth-protected-resource/crm`) |
| Токен другого ресурса (actions→crm) → 401 | да |
| Истёкший токен: клиент сам refresh, ход без следа в диалоге | `smoke G=4/token_refresh` |
| Инъекция в чате | отказ до LLM; audit `guardrail_unsafe` `tr_1d13345dd5d8`; мок Базовый |
| `/admin/down` | чтение тарифа + `guardrail_degraded` `tr_aefe7292ae95`; карточки записи нет |
| `/admin/up` | карточка снова есть |
| Чужой id / роль без записи | `smoke G=4` barrier2 / boris без карточки |

### Аудит и идемпотентность

| Пункт | Итог |
|-------|------|
| Цепочка по `trace_id` | см. `tr_5182afaa002a` выше |
| Двойное «да» | `duplicate_suppressed` `tr_46505b279d5f` |
| `policy_events`: `no_write_scope`, `session_limit`, `guard_not_ok` | все три причины есть в журнале |

### Отказоустойчивость

| Пункт | Итог |
|-------|------|
| actions убит и поднят | следующий ход anna → карточка `p_9f76713b` |
| pdp перезапущен | confirm старой карточки не исполняет смену; свежая сессия снова готовит и исполняет; api/pdp health ok |

## Что нашли и починили

**Факт.** В живом чате olga на «покажи обзор абонента u_4f2a» отвечала «не найден»; в `tool_calls` был вызов `get_subscriber_overview` с `subscriber_id=foreign_id`.

**Гипотеза.** Инструмент не в каталоге data-агента, и модель подставляет placeholder вместо id из реплики.

**Починка.**

1. `app/workers.py` — `get_subscriber_overview` добавлен в tools агента `data` (у клиента по-прежнему скрыт барьером 1).
2. `app/wrap.py` — grounding: из реплики берётся `u_…` и подставляется в `subscriber_id`.
3. `app/planner.py` — в описании data упомянут обзор для оператора.
4. `tests/smoke.py` G=7 — две проверки: инструмент в каталоге + grounding `foreign_id`→`u_4f2a`.

**Подтверждение в журнале.** После фикса: `tool_calls` `get_subscriber_overview` args `{"subscriber_id":"u_4f2a"}` outcome `ok`; ответ чата «Имя: Анна, Тариф: Базовый».

## Матрица сценариев (шаг 8, повтор на приёмке)

`make scenarios` → **11/11** на том же стенде после фикса.

## Оговорки

- Группа `smoke G=7` — доп. к кабинету; после фикса в ней +2 проверки (каталог/grounding overview).
- Автопроверка Хекслета (`hexlet-check` + `HEXLET_ID`) в этом прогоне отдельно не разбиралась: на push ветки смотрим цвет CI.
- README «развернуть за 15 минут» — после шага 10, по образцу прошлого проекта.

## Как повторить

```text
make infra
make migrate
make seed
make stand   # + api, actions, pdp, guardrail, llama-server
make reset
make smoke
make scenarios
```
