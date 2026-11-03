---
paths:
  - "orchestrator/model_router.py"
  - "orchestrator/model_contracts.py"
  - "orchestrator/llm_client.py"
  - "orchestrator/llm_clients/**"
  - "tests/test_model_router.py"
  - "tests/test_anthropic_client.py"
  - "tests/test_claude_code_client.py"
---

# Модели: классы, effort, клиенты

Общая механика шагов (доработка, кэш, эскалация) — в `.claude/rules/steps.md`.

## Маршрутизация моделей по критичности шага

Критичные по содержанию шаги (там, где ошибка стоит дороже — неверная
асимптотика, неверные ограничения, некорректное решение) идут через более
сильную модель; механические/боилерплейт-шаги — через более быструю/дешёвую.

| Шаг                        | Критичность | Предлагаемый класс модели |
|-----------------------------|-------------|----------------------------|
| `constraints_pick`          | высокая     | сильная модель             |
| `solutions_draft`           | высокая     | сильная модель             |
| `checker_draft`              | высокая (при неполном `custom_comparison_notes`) | средняя модель |
| `statement_draft` (LaTeX)   | средняя     | средняя модель              |
| `tests_plan`                | средняя — дизайн adversarial-тестов | средняя модель, high effort |
| `generators_draft`          | низкая — реализация плана | средняя модель, medium effort |
| `test_script_draft`         | низкая — реализация плана | средняя модель, medium effort |
| `interactor_draft`          | высокая — протокол целиком, ошибка ломает все тесты | сильная модель |
| `spec_from_draft` (`init --draft`) | высокая — спек вход всех шагов | сильная модель |
| `tutorial_draft` (`llm tutorial`) | высокая — доказательства по просьбе автора | сильная модель |

`checker_draft` формально механический, когда `custom_comparison_notes`
полон и однозначен (перевод текстового описания в код по паттерну
read_and_check) — но ошибка здесь дороже, чем в среднем механическом шаге:
неверно реализованное сравнение может молча пропускать неверные решения или
браковать верные, и это может остаться незамеченным долго. Средняя модель —
компромисс: если на практике `custom_comparison_notes` часто оказывается
недостаточно точным и шаг слишком часто уходит в `uncertain`
(что само по себе не страшно — так и задумано), можно поднять шаг до сильной
модели, как `constraints_pick`/`solutions_draft`.

Подготовка тестов разбита на `tests_plan` и `generators_draft`/`test_script_draft`
не случайно: реализация генератора по готовой идее механическая, а
**придумывание** adversarial-тестов — как сломать конкретный неверный
алгоритм — не менее содержательно, чем `constraints_pick`; одним вызовом с
высоким effort и десятками файлов в ответе это не укладывалось в таймаут.
Поэтому рассуждение — в `tests_plan` (средняя модель, high effort, небольшой
ответ), код — в `generators_draft`/`test_script_draft` (средняя модель,
medium effort). Если качество предложенных тестов будет слабым, следующий шаг
эскалации — `tests_plan` до сильной модели, как у
`constraints_pick`/`solutions_draft`.

Классы моделей ("strong-model"/"medium-model"/"fast-model") и `effort` на
шаг живут в `orchestrator/model_router.py` — этот модуль вендоронезависим,
он не знает конкретных имён моделей. Соответствие класса конкретной модели
вендора (например, `"strong-model" -> "claude-opus-5"`), авторизация, retry
и формат структурированного вывода — всё это инкапсулировано в реализации
`LLMClient` (`orchestrator/llm_client.py`), по умолчанию `ClaudeCodeClient`
(`orchestrator/llm_clients/claude_code_client.py`: модели через Claude Code
CLI, расход из лимитов подписки, а не с API-баланса — см.
`docs/SETUP.md`); при `ORCHESTRATOR_LLM_BACKEND=anthropic-api` —
`AnthropicClient` (`orchestrator/llm_clients/anthropic_client.py`, те же
модели через Anthropic API). Промпты не должны
зависеть от того, какая именно модель/вендор их выполняет — вся эта
информация не должна течь в `prompts/`.

