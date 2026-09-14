"""Интерфейс вендора LLM, за которым `orchestrator/model_router.py` вызывает
модель — единственная точка, которую нужно реализовать, чтобы подключить
другого вендора (например, локальную модель вместо Anthropic API) или
подменить клиент в тестах.

`model_router.py` знает только про условные классы моделей
("strong-model"/"medium-model"/"fast-model") и `effort`
("low"/"medium"/"high"/"xhigh"/"max") — какой конкретно вендор, какой у него
формат структурированного вывода (tool use, JSON mode, разбор текста) и как
он различает классы моделей и трактует `effort` (маппит на свои модели/
параметры или игнорирует, если вендору это не близко) — целиком инкапсулировано
в реализации `LLMClient`. Пример реализации для Anthropic API —
`orchestrator/llm_clients/anthropic_client.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from orchestrator.model_contracts import ModelResponse


@runtime_checkable
class LLMClient(Protocol):
    """Вызывает одну генеративную модель и возвращает разобранный ответ.

    Реализация сама отвечает за то, как заставить модель вернуть валидный
    JSON по контракту status/artifacts/notes (`docs/PROMPTS.md`) — например,
    через принудительный tool use, structured outputs или разбор текста —
    и за собственные детали вроде авторизации, retry и логирования usage.
    """

    def generate(
        self,
        *,
        model_class: str,
        effort: str,
        system_prompt: str,
        user_prompt: str,
        context_documents: dict[str, str] | None = None,
    ) -> ModelResponse:
        """Вызывает модель `model_class` с заданным `effort` на паре промптов.

        `model_class` — один из ключей `STEP_TO_MODEL` в `model_router.py`
        ("strong-model"/"medium-model"/"fast-model"); как он резолвится в
        конкретную модель вендора — решает сама реализация.
        `effort` — один из `STEP_TO_EFFORT` в `model_router.py`; вендоры без
        понятия effort вольны его игнорировать.
        `context_documents` — карта "путь документа" -> "содержимое"
        (`templates/tutorials/*.md`, `problem_lib.h`, `gen_rand.cpp` и т.п.,
        см. `orchestrator/steps/base.py:STEP_CONTEXT_DOCUMENTS`), которые шаг
        обязан приложить как обязательные правила/примеры — не пересказ по
        памяти. `None`/пустая карта — вести себя как раньше (без документов).
        """
        ...
