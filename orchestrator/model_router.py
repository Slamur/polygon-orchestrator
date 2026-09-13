"""Маршрутизация генеративных шагов по "классу модели" и `effort` (см.
CLAUDE.md, раздел "Маршрутизация моделей по критичности шага").

Этот модуль сознательно вендоронезависим: он знает только условные классы
моделей ("strong-model"/"medium-model"/"fast-model") и уровни `effort`
("low"/"medium"/"high"/"xhigh"/"max") на шаг, но не то, какой конкретно
вендор их обслуживает. Настоящий вызов делает объект `LLMClient`
(`orchestrator/llm_client.py`) — по умолчанию `AnthropicClient`
(`orchestrator/llm_clients/anthropic_client.py`), но его можно подменить
через `set_client(...)` на что угодно другое (локальная модель, другой
вендор), не трогая ни этот модуль, ни промпты, ни шаги.
"""

from __future__ import annotations

from orchestrator.llm_client import LLMClient
from orchestrator.model_contracts import ModelResponse

# Реэкспорт: часть кода и тестов исторически импортирует `ModelResponse`
# отсюда (`from orchestrator.model_router import ModelResponse`).
__all__ = [
    "STEP_TO_MODEL",
    "STEP_TO_EFFORT",
    "ModelResponse",
    "set_client",
    "call_model",
]

# Соответствие шага и условного класса модели. Критичные по содержанию шаги
# (constraints_pick, solutions_draft) — через сильную модель.
# generators_and_script — через среднюю: реализация уже заданных
# generator_ideas механическая, но дизайн adversarial-тестов под
# `solutions.known_wrong_approaches` — самостоятельное рассуждение о том, как
# сломать конкретный неверный алгоритм, не менее содержательное, чем
# constraints_pick/solutions_draft (см. CLAUDE.md, "Маршрутизация моделей по
# критичности шага") — поэтому шаг поднят с быстрой модели до средней; если
# качество предложенных тестов окажется слабым, следующий шаг эскалации —
# до сильной модели.
# statement_draft — средняя модель: задача там в основном про форматирование
# по явным правилам из requirements.md/polygon.md, а не про рассуждение, так
# что Opus-уровень для неё не нужен (см. CLAUDE.md).
STEP_TO_MODEL: dict[str, str] = {
    "statement_draft": "medium-model",
    "constraints_pick": "strong-model",
    "generators_and_script": "medium-model",
    "solutions_draft": "strong-model",
}

# Уровень effort на шаг (общее для вендоров, поддерживающих такую ручку;
# кто не поддерживает — вправе игнорировать). constraints_pick/
# solutions_draft — самые рискованные по цене ошибки; generators_and_script —
# high из-за дизайна adversarial-тестов; statement_draft — medium, это
# оформление, а не рассуждение.
STEP_TO_EFFORT: dict[str, str] = {
    "statement_draft": "medium",
    "constraints_pick": "high",
    "generators_and_script": "high",
    "solutions_draft": "high",
}

_client: LLMClient | None = None


def set_client(client: LLMClient) -> None:
    """Явно подменяет `LLMClient`, который использует `call_model`.

    Точка расширения под других вендоров (локальная модель, другой API) или
    под тестовые дублёры — вызывающий код (например, `cli.py` или тест)
    решает, каким клиентом пользоваться, `model_router.py` сам к конкретному
    вендору не привязан.
    """
    global _client
    _client = client


def _default_client() -> LLMClient:
    # Ленивый импорт: если клиент явно подменили через `set_client`, модуль
    # ни разу не тронет `anthropic`/`python-dotenv` и не потребует
    # ANTHROPIC_API_KEY — это вендорский пакет, а не общая зависимость
    # оркестратора.
    from orchestrator.llm_clients.anthropic_client import AnthropicClient

    return AnthropicClient()


def _get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = _default_client()
    return _client


def call_model(step: str, system_prompt: str, user_prompt: str) -> ModelResponse:
    """Вызывает модель, назначенную шагу `step` через `STEP_TO_MODEL`/`STEP_TO_EFFORT`.

    `system_prompt` и `user_prompt` — уже отрендеренные тексты промптов
    (`prompts/<step>/system.md` и `prompts/<step>/user.md.j2` после
    подстановки контекста); эта функция их не читает и не рендерит сама.

    Сама модель вызывается через текущий `LLMClient` (см. `set_client`) —
    как именно он добивается структурированного JSON-ответа по контракту
    status/artifacts/notes (`docs/PROMPTS.md`) — решает реализация клиента,
    не этот модуль.
    """
    client = _get_client()
    return client.generate(
        model_class=STEP_TO_MODEL[step],
        effort=STEP_TO_EFFORT[step],
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
