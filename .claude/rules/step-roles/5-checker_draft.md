---
paths:
  - "orchestrator/steps/checker_draft.py"
  - "prompts/checker_draft/**"
  - "tests/test_step_checker_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Роль каждого генеративного шага, пункт 5: `checker_draft`

Общее для всех шагов (режим доработки, конфликты, кэш, эскалация,
предложения сверх спека) — в `.claude/rules/steps.md`, модели — в
`.claude/rules/models.md`, команды `llm` — в `.claude/rules/llm-commands.md`.

5. **`checker_draft`** (опциональный, как и `solutions_draft`) — запускается,
   только если `checker.custom_needed: true` в спеке; если используется
   стандартный чекер (`checker.standard`, например `ncmp`) — шаг не
   запускается вообще, генерировать нечего. Вход: `checker.custom_comparison_notes`
   (самодостаточное текстовое описание формата ответа и правила сравнения —
   автор пишет его сам, шаг ничего не выводит из условия/легенды
   самостоятельно). Выход: `checker.cpp` по паттерну read_and_check (как в
   `checker.cpp` учебной задачи `examples/array/prefix-sums`) — функция `read_answer(InStream&)`,
   параметризуемая потоком (используется для `ans` и `ouf` одинаково),
   которая читает И валидирует ответ, оборачивается в `read_answer_from`
   с проверкой `seekEof()` (лишние данные после ответа → `_pe`). Модель
   реализует ровно то сравнение, которое описано в
   `custom_comparison_notes` — если оно неполно (например, не указано,
   чувствительно ли сравнение к регистру, или что делать с пустым вводом),
   шаг обязан вернуть `status: uncertain`, а не домысливать поведение
   чекера — ошибка здесь особенно дорога: чекер, который слишком мягко или
   слишком строго сравнивает ответы, может пропустить неверное решение или
   забраковать верное.
