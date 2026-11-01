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
- data — данные абонента: профиль, текущий тариф, каталог тарифов, списания; у оператора — обзор чужого абонента.
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


_ACTION_STEMS = (
    "смен", "измен", "поменя", "перевед", "переключ", "перейд", "перенес",
    "включ", "подключ", "отключ", "выключ", "активир", "деактивир",
    "обнов", "постав", "установ", "добав", "убер", "удал", "верни",
)
# намерение без глагола действия: «хочу семейный», «давай на игровой»
_INTENT_STEMS = (
    "хочу", "хотел", "нужно", "нужен", "надо", "давай", "пожалуйста",
    "можешь", "помоги", "оформи", "сделай",
)
_QUESTION_WORDS = (
    "как", "почему", "что", "какой", "какая", "какие", "какое", "сколько",
    "зачем", "когда", "где", "расскажи", "объясни", "поясни", "скажи",
)
_NEGATIVE_MARKERS = ("спасибо", "не меняй", "ничего не", "не надо", "не нужно")


_DATA_HINTS = ("тариф", "профиль", "баланс", "списан", "номер", "абонент")
_KB_HINTS = ("почему", "правил", "как ", "роуминг", "услов", "входит", "сколько стоит")


def route(message: str, *, allow_write: bool = True) -> Plan | None:
    """Явный маршрут без LLM. None — пусть решает планировщик.

    Чёткий вопрос о своих данных — только data. Вопрос о правилах — kb.
    «Почему списание» — оба. Глагол смены — tariff, если роль может писать.
    Неясное («хочу семейный» без глагола) не угадываем: планировщик + гейт.
    """
    text = (message or "").strip().lower()
    if not text:
        return None
    steps: list[Step] = []
    if any(stem in text for stem in _ACTION_STEMS):
        if allow_write:
            steps = [Step(agent="tariff", task=message, why="route", action=True)]
        else:
            steps = [
                Step(agent="data", task=message, why="route", action=False),
                Step(agent="kb", task=message, why="route", action=False),
            ]
        return Plan(steps=steps)
    if wants_write(text):
        return None
    need_data = any(h in text for h in _DATA_HINTS)
    need_kb = any(h in text for h in _KB_HINTS)
    if need_data and need_kb:
        steps = [
            Step(agent="data", task=message, why="route", action=False),
            Step(agent="kb", task=message, why="route", action=False),
        ]
    elif need_kb:
        steps = [Step(agent="kb", task=message, why="route", action=False)]
    elif need_data:
        steps = [Step(agent="data", task=message, why="route", action=False)]
    else:
        return None
    return Plan(steps=steps)


def wants_write(message: str) -> bool:
    """Детерминированно: реплика просит изменение, а не справку.

    Чтение: реплика начинается с вопроса/«расскажи», это благодарность или
    отмена, либо заканчивается «?» без слова действия. Запись: слово
    действия или намерение («хочу…», «давай…»). Прочее — не режем:
    решает планировщик, а последнее слово всё равно за карточкой и политикой.
    """
    text = (message or "").strip().lower()
    if not text:
        return False
    first = text.split()[0].strip(",.!?:;«»\"'")
    if first in _QUESTION_WORDS:
        return False
    if any(m in text for m in _NEGATIVE_MARKERS):
        return False
    if any(stem in text for stem in _ACTION_STEMS):
        return True
    if text.endswith("?"):
        return any(stem in text for stem in _INTENT_STEMS)
    return True


def gate(
    plan: Plan,
    message: str | None = None,
    *,
    allow_write: bool = True,
) -> tuple[Plan, list[str]]:
    """Оставить только известных агентов; у чтения снять action.

    С message: шаг tariff без слов действия в реплике отбрасывается —
    карточку создаёт просьба абонента, а не настроение планировщика.
    Без allow_write шаг tariff тоже отбрасывается (нет prepare_* у роли).
    Пустой план → чтение data+kb по реплике; уточняющий вопрос планировщика
    сохраняется только для просьбы изменить (чтение безвредно, уточнять нечего).
    """
    kept: list[Step] = []
    dropped: list[str] = []
    for step in plan.steps:
        name = (step.agent or "").strip().lower()
        if name not in KNOWN_AGENTS:
            dropped.append(name or "?")
            continue
        if name == "tariff" and not allow_write:
            dropped.append(name)
            continue
        if message is not None and name == "tariff" and not wants_write(message):
            dropped.append(name)
            continue
        # пометка action только у tariff/tickets; у чтения всегда снимается
        action = name in ACTION_AGENTS
        kept.append(
            Step(agent=name, task=step.task, why=step.why or "", action=action)
        )
    question = plan.question
    if message is not None and not kept and (not question or not wants_write(message)):
        kept = [
            Step(agent="data", task=message, why="fallback", action=False),
            Step(agent="kb", task=message, why="fallback", action=False),
        ]
        question = None
    return Plan(steps=kept, question=question), dropped


async def next_plan(planner, history: list, message: str) -> tuple[Plan, list]:
    """Один вызов планировщика по истории диалога → план и новая история."""
    from app import llm_pool

    if history:
        payload: list | str = list(history) + [{"role": "user", "content": message}]
    else:
        payload = message
    # тот же пул, что у run_agent: клиент+model, без пересборки агента
    result, _meta = await llm_pool.run_with_failover(planner, payload)
    plan = result.final_output
    if not isinstance(plan, Plan):
        plan = result.final_output_as(Plan)
    return plan, result.to_input_list()
