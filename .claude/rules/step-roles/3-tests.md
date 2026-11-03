---
paths:
  - "orchestrator/steps/tests_plan.py"
  - "prompts/tests_plan/**"
  - "tests/test_step_tests_plan.py"
  - "orchestrator/steps/generators_draft.py"
  - "prompts/generators_draft/**"
  - "tests/test_step_generators_draft.py"
  - "orchestrator/steps/test_script_draft.py"
  - "prompts/test_script_draft/**"
  - "tests/test_step_test_script_draft.py"
  - "templates/tests_plan_format.yaml"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Роль каждого генеративного шага, пункт 3: `tests_plan`, `generators_draft`, `test_script_draft`

Общее для всех шагов (режим доработки, конфликты, кэш, эскалация,
предложения сверх спека) — в `.claude/rules/steps.md`, модели — в
`.claude/rules/models.md`, команды `llm` — в `.claude/rules/llm-commands.md`.

3. **Подготовка тестов** — три шага: содержательная часть отдельно от
   объёмной, чтобы ни один вызов модели не делал и то и другое сразу.
   - **`tests_plan`** — вход: секция `generation` спека + формат ввода из
     `statement_draft` (`formal_input_sketch`, `indexing`, input примеров —
     отдельного поля `generation.input_shape` в спеке нет, чтобы не
     дублировать описание формата входа) + `legend_sketch` + зафиксированные
     ограничения из шага 2 без TL/ML + (если секция `solutions` в спеке
     задана) `solutions.known_wrong_approaches` и `solutions.algorithm_hint`.
     Выход: план тестов `authoring/tests_plan.yaml` по формату
     `templates/tests_plan_format.yaml` — генераторы (имя, параметры с
     типами, что читают и печатают, алгоритм) и блоки test-script'а
     (команды-цепочки генераторов, именованные значения, группа, какие
     пункты спека блок покрывает — `covers`). План самодостаточен:
     следующие два шага спека не видят. Оркестратор проверяет его до записи
     (`parse_tests_plan`: команды вызывают только генераторы плана и только
     их параметры; не разбирается — конфликт) и сообщает заметками
     `uncovered` о пунктах `specific_test_ideas`/`known_wrong_approaches`,
     которые не покрыты ни одним блоком, и о генераторах без тестов. Внутри
     шаг делает две разные по характеру вещи, и это важно не смешивать:
     если `generation.generator_ideas` уже описывает нужный генератор — это
     механическая часть; для каждого пункта `known_wrong_approaches` шаг
     обязан сам придумать тест (и, если нужно, генератор), целенаправленно
     проверяющий именно этот сценарий, и пометить его `status: proposed` с
     обоснованием в `rationale` (почему этот тест ловит именно такую
     ошибку) — как и с любым другим предположением модели, это не
     самостоятельное решение, а предложение для проверки автором. Если ни
     `generator_ideas`, ни `known_wrong_approaches` не дают зацепок для
     стресс-тестов — шаг не должен молчать об этом: явно указать в `notes`,
     что adversarial-случаи не придуманы и нужна ручная проверка. Сверх
     заказанного автором шаг может предложить свои тесты под вероятные
     ошибки, которых нет в спеке (по легенде и `algorithm_hint`) — см.
     "Предложения модели сверх спека" в `.claude/rules/steps.md`.
   - **`generators_draft`** — вход: генераторы плана (каждый — отдельная
     часть входа: правка одной записи плана — правка одного генератора в
     режиме доработки, правка тестов плана шаг не трогает) + формат ввода из
     `statement_draft` + `constraints.yaml` без TL/ML (гарантии входа,
     которые генератор обязан соблюдать). Выход: `generators/<name>.cpp` на
     каждый генератор плана (на основе `generators/*.cpp` из референсов
     спека); недостающие дозапрашиваются, о генераторах вне плана и
     параметрах `opt<T>(...)`, отличных от плана, — заметки `plan_mismatch`.
     Если запись плана не реализуется однозначно — `uncertain`: план правит
     автор.
   - **`test_script_draft`** — вход: `constants`, `tests` и сигнатуры
     генераторов плана (без алгоритмов — правка кода генератора скрипт не
     инвалидирует) + `constraints.yaml` без TL/ML + `generation.script_style`.
     Выход: FreeMarker test-script (`flat` — `test_script` по образцу из
     референсов; `groups` — `test_script_groups` по группам из `group`
     блоков плана, на той же структуре, если образца нет).
