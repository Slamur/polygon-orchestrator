---
paths:
  - "orchestrator/steps/interactor_draft.py"
  - "prompts/interactor_draft/**"
  - "tests/test_step_interactor_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Роль каждого генеративного шага, пункт 6: `interactor_draft`

Общее для всех шагов (режим доработки, конфликты, кэш, эскалация,
предложения сверх спека) — в `.claude/rules/steps.md`, модели — в
`.claude/rules/models.md`, команды `llm` — в `.claude/rules/llm-commands.md`.

6. **`interactor_draft`** (опциональный) — запускается, только если задача
   интерактивная (в спеке есть секция `interactor`). Вход:
   `interactor.behavior_notes` (адаптивность, проверка итогового ответа,
   ошибки решения, что писать в `tout` для чекера) + из `statement_draft`
   формат теста (`formal_input_sketch`, `indexing`, input примеров — в
   интерактивной задаче это вход интерактора, а не решения) и протокол
   `interaction_sketch`. Выход: `interactor.cpp` (testlib,
   `registerInteraction`). Как и у чекера, модель реализует ровно
   описанное поведение; чего не хватает — `uncertain`. Протокол
   интерактивной задачи также получают `statement_draft` (пишет
   `interaction.tex` и диалог каждого примера — что решение прочитало и
   что вывело, для "input/output for statements" на Polygon) и
   `solutions_draft`; правила — `templates/tutorials/interactive.md`,
   которые эти два шага получают только у интерактивной задачи.
