---
paths:
  - "orchestrator/steps/constraints_pick.py"
  - "prompts/constraints_pick/**"
  - "tests/test_step_constraints_pick.py"
  - "orchestrator/steps/limits_from_constraints.py"
  - "prompts/limits_from_constraints/**"
  - "tests/test_step_limits_from_constraints.py"
  - "orchestrator/limits.py"
  - "orchestrator/step_order.py"
  - "docs/PROMPTS.md"
  - "tests/test_prompts_doc_sync.py"
---

# Роль каждого генеративного шага, пункт 2: `constraints_pick` и `limits_from_constraints`

Общее для всех шагов (режим доработки, конфликты, кэш, эскалация,
предложения сверх спека, модели) — в `.claude/rules/steps.md`, команды `llm` —
в `.claude/rules/llm-commands.md`.

2. **`constraints_pick`** — вход: секция `constraints` спека, в первую
   очередь `intended_complexity` и `complexity_reasoning`, + формат ввода из
   `statement_draft` (`formal_input_sketch`, `indexing`, input примеров —
   источник порядка чтения для валидатора; легенда не передаётся). Выход:
   зафиксированные диапазоны переменных, TL/ML, разбиение на тестовые
   группы (в `constraints.yaml`, включая секцию `validator` —
   `constants`/`checks`, описывающую, что именно должен проверять
   валидатор, по формату `templates/constraints_format.yaml`) +
   `validator.cpp` (по образцу `validator.cpp` из референсов, если есть),
   реализующий 1-в-1 `validator.constants`/`validator.checks`, которые шаг
   сам же и зафиксировал в `constraints.yaml` — никаких проверок сверх или
   помимо перечисленных там. `problem_id` в `constraints.yaml` оркестратор
   ставит из спека поверх ответа модели. Генерация `validator.cpp` — не отдельный шаг,
   а последний под-шаг того же рассуждения об ограничениях в рамках одного
   вызова модели: валидатор целиком выводится из решений, которые
   `constraints_pick` и так принимает, и раздельные шаги/вызовы рисковали бы
   разойтись между `constraints.yaml` и `validator.cpp`. Модель **не имеет
   права** сама придумывать асимптотику по легенде — если
   `intended_complexity` пуст, шаг обязан завершиться ошибкой
   "недостаточно данных", а не угадывать; в этом случае `validator.cpp`
   тоже не пишется — весь ответ шага уходит в `uncertain` целиком.
   После `constraints_pick` идёт механический шаг **`limits_from_constraints`**
   (без модели, `step_order.MECHANICAL_STEPS`): пишет `limits.yaml` —
   актуальные TL/ML задачи, которые загружает `polygon push` и обновляет
   `polygon pull`, — из TL/ML `constraints.yaml`. Его вход — только эти
   TL/ML, поэтому `limits.yaml` перезаписывается (прежний — в
   `.llm_backup/`), лишь когда они изменились: TL/ML, поправленные на Polygon
   или руками, переживают `llm generate`. Если `limits.yaml` уже есть, а шаг
   ещё не запускался (файл скачал `polygon pull`), файл принимается как есть;
   `llm generate --step limits_from_constraints --force` выводит его заново.
