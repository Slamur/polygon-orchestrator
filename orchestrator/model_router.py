"""Маршрутизация генеративных шагов по "классу модели" (см. CLAUDE.md,
раздел "Маршрутизация моделей по критичности шага").

Это только структура и заглушка: реальный вызов API (HTTP/SDK, ключи,
retry) — отдельная будущая задача. Промпты не должны зависеть от того,
какая именно модель их выполняет — поэтому классы модели здесь условные
("strong-model" / "medium-model" / "fast-model"), а не имена конкретных
моделей.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Соответствие шага и условного класса модели. Критичные по содержанию шаги
# (constraints_pick, solutions_draft) — через сильную модель.
# generators_and_script — через среднюю: реализация уже заданных
# generator_ideas механическая, но дизайн adversarial-тестов под
# `solutions.known_wrong_approaches` — самостоятельное рассуждение о том, как
# сломать конкретный неверный алгоритм, не менее содержательное, чем
# constraints_pick/solutions_draft (см. CLAUDE.md, "Маршрутизация моделей по
# критичности шага") — поэтому шаг больше не считается чисто механическим и
# поднят с быстрой модели до средней; если качество предложенных тестов
# окажется слабым, следующий шаг эскалации — до сильной модели.
# statement_draft пока тоже на сильной модели: выбор между сильной и средней
# по бюджету ещё не сделан (см. CLAUDE.md), сильная модель — безопасный
# дефолт до этого решения.
STEP_TO_MODEL: dict[str, str] = {
    "statement_draft": "strong-model",
    "constraints_pick": "strong-model",
    "generators_and_script": "medium-model",
    "solutions_draft": "strong-model",
}


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


def call_model(step: str, system_prompt: str, user_prompt: str) -> ModelResponse:
    """Вызывает модель, назначенную шагу `step` через `STEP_TO_MODEL`.

    `system_prompt` и `user_prompt` — уже отрендеренные тексты промптов
    (`prompts/<step>/system.md` и `prompts/<step>/user.md.j2` после
    подстановки контекста); эта функция их не читает и не рендерит сама.

    Должна вернуть `ModelResponse`, разобранный из JSON-ответа модели по
    контракту status/artifacts/notes (`docs/PROMPTS.md`).

    Сейчас не реализовано: реальный вызов API добавляется отдельной задачей
    (см. CLAUDE.md, "Маршрутизация моделей по критичности шага") — этот
    модуль сознательно не тянет никаких SDK/HTTP-зависимостей.
    """
    raise NotImplementedError(
        f"call_model('{step}'): реальный вызов API добавляется отдельной "
        "задачей — этот модуль пока только объявляет маршрутизацию "
        "(STEP_TO_MODEL) и контракт ответа (ModelResponse)"
    )
