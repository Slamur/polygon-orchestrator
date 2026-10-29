"""Polygon-шаг — загрузка файлов задачи, которые нужны только оркестратору и
автору, в Polygon как aux-файлов (`problem.saveFile`, `type=aux` — раздел
"Attachments" на странице Files) — файлы `authoring/` каталога задачи, на
Polygon — по имени файла, без каталога:

- `spec.yaml` — спек задачи, вход генеративных шагов (обязателен);
- `constraints.yaml` — результат `constraints_pick` целиком (диапазоны,
  группы, секция `validator`), как его записал шаг; актуальные TL/ML
  задачи переносит `set_constraints` из `limits.yaml`. Файл, который прежние
  версии `polygon pull` собирали из одних TL/ML (`PULLED_CONSTRAINTS_HEADER`),
  — не результат `constraints_pick` и не загружается;
- `tests_plan.yaml` — план тестов (шаг `tests_plan`, возможно с правками
  автора), по которому написаны генераторы и test-script;
- `draft<расширение>` — копия черновика автора, по которому
  `init --draft` написал спек.

Всё это хранится на Polygon, чтобы `polygon pull` мог восстановить задачу
целиком. Файлы загружаются как есть, побайтно (`ctx.spec` — уже разобранный
спек — не используется: комментарии и форматирование автора должны
сохраниться). Генеративного шага-зависимости нет — только `create_problem`.
Файл, удалённый локально, на Polygon остаётся — шаг его не удаляет.

Запускается через `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.steps.base import (
    NO_CHANGES_MESSAGE,
    PolygonStep,
    PolygonStepError,
    StepContext,
    StepResult,
    changed_items,
    never_done,
    require_polygon_state,
    sync_status,
)
from orchestrator.polygon.sync import (
    CONSTRAINTS_KEY,
    PULLED_CONSTRAINTS_HEADER,
    SPEC_KEY,
    TESTS_PLAN_KEY,
    digest,
    is_draft_name,
)
from orchestrator.problem_paths import DRAFT_STEM, authoring_dir

STEP_NAME = "upload_attachments"


def read_attachments(problem_id: str, problems_dir: Path) -> list[tuple[str, bytes]]:
    """[(имя, содержимое), ...]: спек, `constraints.yaml` (если он есть и это
    результат `constraints_pick`), `tests_plan.yaml` (если есть), копии
    черновика по алфавиту. Имя — и имя
    aux-файла на Polygon, и ключ базы синхронизации. Нет спека —
    `PolygonStepError`."""
    directory = authoring_dir(Path(problems_dir) / problem_id)
    spec_path = directory / SPEC_KEY
    if not spec_path.exists():
        raise PolygonStepError(f"{spec_path} not found")
    attachments = [(SPEC_KEY, spec_path.read_bytes())]

    constraints_path = directory / CONSTRAINTS_KEY
    if constraints_path.is_file():
        content = constraints_path.read_bytes()
        if not content.startswith(PULLED_CONSTRAINTS_HEADER.encode("utf-8")):
            attachments.append((CONSTRAINTS_KEY, content))

    tests_plan_path = directory / TESTS_PLAN_KEY
    if tests_plan_path.is_file():
        attachments.append((TESTS_PLAN_KEY, tests_plan_path.read_bytes()))

    drafts = sorted(
        path
        for path in directory.glob(f"{DRAFT_STEM}*")
        if path.is_file() and is_draft_name(path.name)
    )
    attachments.extend((path.name, path.read_bytes()) for path in drafts)
    return attachments


def _items(attachments: list[tuple[str, bytes]]) -> dict[str, str]:
    return {name: digest(content) for name, content in attachments}


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    polygon_id = require_polygon_state(ctx).polygon_id
    attachments = read_attachments(ctx.problem_id, ctx.problems_dir)
    changed = changed_items(ctx, _items(attachments))
    if not changed:
        return StepResult(NO_CHANGES_MESSAGE)

    sent = []
    for name, content in attachments:
        if name not in changed:
            continue
        client.call(
            "problem.saveFile",
            {"problemId": str(polygon_id), "type": "aux", "name": name, "file": content},
        )
        sent.append(name)
    return StepResult(
        f"uploaded {', '.join(sent)} as attachment(s) to Polygon id={polygon_id}", changed
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=(SPEC_KEY, CONSTRAINTS_KEY, TESTS_PLAN_KEY, DRAFT_STEM),
        read_items=lambda: _items(read_attachments(problem_id, problems_dir)),
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
