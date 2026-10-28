"""Планировщик: план запусков доменных агентов до всякого исполнения.

План — структурированный выход LLM, но исполнению он не принадлежит:
гейт оркестратора оставляет только шаги известных агентов. Действия
(смена тарифа, услуги, контакты, тикет) идут через карточку подтверждения
и политику прав, а не напрямую.

TODO(шаг 1):
  1. Схемы Step и Plan (pydantic):
     - Step: agent (алиас agentName), task (алиас job), why, action: bool
       — алиасы нужны: модели отвечают своим диалектом имён;
     - Plan: steps: list[Step], question: str | None (уточняющий вопрос,
       если данных для плана мало).
  2. PLANNER_PROMPT — каталог агентов и правила планирования:
     вопрос о данных абонента — один шаг data; правила и последствия —
     шаг kb; «почему списание» — оба; изменения (смена тарифа, услуги,
     контакты) — шаг tariff с action=true; вне тем оператора — tickets
     с action=true; названия тарифов — строчными без кавычек.
  3. build_planner() — агент со структурированным выходом Plan
     (app.llm.build_agent(..., output_type=Plan)).
  4. gate(plan) — гейт: action-шаги только tariff/tickets, у read-агентов
     action снимается, неизвестные агенты отбрасываются (причина — в трейс).
  5. next_plan(planner, history, message) — один вызов Runner.run
     (через пул llm_pool, если он включён) → (Plan, история хода).
"""
from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from agents import Runner

from app import llm

READ_AGENTS = ("data", "kb")
ACTION_AGENTS = ("tariff", "tickets")
KNOWN_AGENTS = set(READ_AGENTS) | set(ACTION_AGENTS)


class Step(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent: str = Field(validation_alias=AliasChoices("agentName", "agent"))
    task: str = Field(validation_alias=AliasChoices("job", "task"))
    why: str = ""
    action: bool = False


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[Step] = []
    question: str | None = None


PLANNER_PROMPT = """Ты планировщик AI-оператора мобильной связи.
Составь план из шагов: каких узких агентов вызвать и с какой задачей.
Не отвечай абоненту сам — только план (или уточняющий вопрос).

Каталог агентов:
- data — данные абонента: профиль, текущий тариф, каталог тарифов, списания.
- kb — база знаний: правила списаний, смены тарифа, услуги, FAQ. Указывает источник.
- tariff — изменение тарифа / услуг / контактов. Всегда action=true. Сам не исполняет — готовит карточку.
- tickets — обращение в поддержку / темы вне оператора. Всегда action=true.

Правила:
- вопрос о данных абонента (тариф, профиль, списания) — шаг data;
- правила, последствия, «как/почему» по регламенту — шаг kb;
- «почему списание» / тариф и причина суммы — оба: data и kb;
- смена тарифа, услуги, контакты — шаг tariff с action=true;
- вне тем оператора или нужна поддержка — tickets с action=true;
- названия тарифов пиши строчными без кавычек (базовый, семейный, …);
- если не хватает данных для плана — заполни question, steps оставь пустым;
- не выдумывай агентов вне каталога.
"""


def build_planner():
    return llm.build_agent("planner", PLANNER_PROMPT, output_type=Plan)


def gate(plan: Plan) -> tuple[Plan, list[str]]:
    """Оставить только известных агентов; у чтения снять action."""
    kept: list[Step] = []
    dropped: list[str] = []
    for step in plan.steps:
        name = (step.agent or "").strip().lower()
        if name not in KNOWN_AGENTS:
            dropped.append(name or "?")
            continue
        action = bool(step.action)
        if name in READ_AGENTS:
            action = False
        elif name not in ACTION_AGENTS:
            action = False
        kept.append(
            Step(agent=name, task=step.task, why=step.why or "", action=action)
        )
    return Plan(steps=kept, question=plan.question), dropped


async def next_plan(planner, history: list, message: str) -> tuple[Plan, list]:
    """Один вызов планировщика по истории диалога → план и новая история."""
    if history:
        payload: list | str = list(history) + [{"role": "user", "content": message}]
    else:
        payload = message
    result = await Runner.run(planner, payload)
    plan = result.final_output
    if not isinstance(plan, Plan):
        plan = result.final_output_as(Plan)
    return plan, result.to_input_list()
