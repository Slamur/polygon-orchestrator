"""Имена шагов обоих пайплайнов в порядке выполнения — отдельно от самих
шагов и без единого тяжёлого импорта.

CLI (`cli.py`) строит парсер (`choices` у `--step`) при каждом запуске, в
том числе при каждом нажатии Tab в shell-автодополнении, и не должен ради
списка имён тянуть сами шаги, а с ними — pydantic и requests. Соответствие
имён реальным шагам (`pipeline._STEP_MODULES`, `polygon.pipeline._POLYGON_STEPS`)
проверяется тестами.
"""

from __future__ import annotations

# Порядок шагов фиксирован (CLAUDE.md, "Роль каждого генеративного шага"):
# каждый следующий шаг может зависеть от артефактов предыдущих. `checker_draft`
# ни от чего не зависит (в отличие от соседей по порядку), но держится рядом
# с `generators_and_script` — логически это тоже часть тестовой инфраструктуры
# задачи, а не решений (CLAUDE.md, пункт 5). `interactor_draft` — туда же и по
# той же причине (CLAUDE.md, пункт 6).
STEP_ORDER: list[str] = [
    "statement_draft",
    "constraints_pick",
    "generators_and_script",
    "checker_draft",
    "interactor_draft",
    "solutions_draft",
]

# Список расширяется по мере добавления шагов — дописывать новые имена (как
# правило, в конец) и добавлять запись в `polygon.pipeline._POLYGON_STEPS`;
# больше ничего менять не нужно, если новый шаг — такой же `PolygonStep`,
# запускаемый через `steps.base.run_step`. `upload_attachments` (спек,
# `constraints.yaml`, черновик) идёт сразу после `create_problem`: спек —
# основа задачи, и он должен оказаться на Polygon, даже если какой-то из
# следующих шагов упадёт. `upload_interactor` — после
# `set_constraints`: тот выставляет флаг интерактивности задачи.
POLYGON_STEP_ORDER: list[str] = [
    "create_problem",
    "upload_attachments",
    "set_constraints",
    "upload_problem_lib",
    "upload_validator",
    "upload_checker",
    "upload_interactor",
    "upload_statement",
    "upload_generators",
    "upload_test_script",
    "upload_solutions",
]
