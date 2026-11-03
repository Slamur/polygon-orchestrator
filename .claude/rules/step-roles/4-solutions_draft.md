---
paths:
  - "orchestrator/steps/solutions_draft.py"
  - "prompts/solutions_draft/**"
  - "tests/test_step_solutions_draft.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Роль каждого генеративного шага, пункт 4: `solutions_draft`

Общее для всех шагов (режим доработки, конфликты, кэш, эскалация,
предложения сверх спека, модели) — в `.claude/rules/steps.md`, команды `llm` —
в `.claude/rules/llm-commands.md`.

4. **`solutions_draft`** — вход: секция `solutions` спека +
   `constraints.intended_complexity` + текст `constraints.yaml` из
   шага 2 без TL/ML (вердикты по времени модель выводит из асимптотики и
   максимальных значений переменных, а подгонка TL не инвалидирует решения)
   + условие из `statement_draft` в части ввода-вывода
   (`formal_input_sketch`, `formal_output_sketch`, `indexing`, примеры с
   ответами, `legend_sketch` — для понимания терминов `algorithm_hint`, не
   как источник алгоритма авторского решения). Выход: черновики решений с
   заявленными вердиктами (ok/wa/tl/tl_ok), которые автор обязательно
   перепроверяет и вероятно переписывает сам — это не финальные решения.
   Сверх решений, описанных в спеке, шаг может предложить свои (другое или
   более быстрое `ok`, вероятные ошибки участников) — см. "Предложения
   модели сверх спека" в `.claude/rules/steps.md`.

У интерактивной задачи шаг получает и протокол взаимодействия — см.
`.claude/rules/step-roles/6-interactor_draft.md`.
