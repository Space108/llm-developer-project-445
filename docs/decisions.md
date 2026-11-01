# Архитектурные решения

Полные записи — в `docs/adr/`.

1. [Guardrail и политика](adr/0001-guardrail-and-policy.md) — текст проверяет сервис, права решает Dogwood; fail-closed на действиях.
2. [Гейт планировщика](adr/0002-planner-gate.md) — карточку создаёт просьба абонента, а не настроение Qwen-7B.
3. [Подстановка имён в prepare](adr/0003-prepare-grounding.md) — имя тарифа/услуги берём из реплики, если модель подменила.
4. [Принудительный tool_choice у write-агентов](adr/0004-tool-choice-required.md) — tariff/tickets обязаны вызвать инструмент, а не болтать.
