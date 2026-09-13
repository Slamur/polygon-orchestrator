"""Вендоронезависимый контракт ответа генеративного шага.

`ModelResponse` — единственное, что должно быть общим между
`orchestrator/model_router.py` и любой реализацией `LLMClient`
(`orchestrator/llm_client.py`): сам JSON-контракт status/artifacts/notes
описан в `docs/PROMPTS.md` и не зависит от того, какой именно вендор
(Anthropic API, локальная модель, что угодно ещё) его наполняет. Вынесено в
отдельный модуль, чтобы не создавать цикл импортов между `model_router.py`
(которому нужен тип `LLMClient`) и `llm_client.py`/конкретными клиентами
(которым нужен тип `ModelResponse`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelResponse:
    """Разобранный ответ модели по JSON-контракту из `docs/PROMPTS.md`.

    `status` — "confirmed" | "proposed" | "uncertain".
    `artifacts` — словарь "имя файла" -> "содержимое" (может быть пустым или
    частичным при `status == "uncertain"`).
    `notes` — список заметок вида {"field", "kind", "explanation"}, где
    `kind` — "proposed" | "uncertain".
    """

    status: str
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[dict[str, Any]] = field(default_factory=list)
