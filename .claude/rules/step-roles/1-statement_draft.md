---
paths:
  - "orchestrator/steps/statement_draft.py"
  - "prompts/statement_draft/**"
  - "tests/test_step_statement_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Роль каждого генеративного шага, пункт 1: `statement_draft`

Общее для всех шагов (режим доработки, конфликты, кэш, эскалация,
предложения сверх спека) — в `.claude/rules/steps.md`, модели — в
`.claude/rules/models.md`, команды `llm` — в `.claude/rules/llm-commands.md`.

1. **`statement_draft`** — вход: секция `statement_draft` спека и язык
   условия (`language`; в хеше — только не `ru`). Выход:
   черновик условия на этом языке в `problems/<путь задачи>/statement/<language>/` — отдельные `.tex` на
   легенду/ввод/вывод/примечание (набор имён проверяется до записи на
   диск), следующий структуре и стилистическим правилам из
   `templates/tutorials/statement.md`. Input каждого примера в
   `examples/example_<N>.txt` — копия из `sample_examples`: его пишет
   оркестратор, а не модель (`GeneratedArtifacts` в
   `orchestrator/steps/base.py`); файлы примеров, которых больше нет в
   спеке, помечаются `.removed`.

У интерактивной задачи шаг получает и протокол взаимодействия — см.
`.claude/rules/step-roles/6-interactor_draft.md`.
