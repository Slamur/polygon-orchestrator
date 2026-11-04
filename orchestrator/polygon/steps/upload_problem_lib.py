"""Polygon step 3 — uploading `problems/<problem_id>/problem_lib.h` to Polygon
as a resource file (`problem.saveFile`, `type=resource`).

Each problem has its own copy of `problem_lib.h` (see `PROBLEM_LIB_FILENAME` in
`orchestrator/steps/base.py`): if it doesn't exist yet, the step first copies
`templates/problem_lib.h` there.
There's no generative step dependency — only `create_problem`.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
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
from orchestrator.polygon.sync import PROBLEM_LIB_KEY, digest
from orchestrator.steps.base import (
    PROBLEM_LIB_FILENAME,
    TEMPLATES_DIR,
    ensure_problem_lib,
    problem_lib_path,
    template_problem_lib_path,
)

STEP_NAME = "upload_problem_lib"


def make_step(templates_dir: Path = TEMPLATES_DIR) -> PolygonStep:
    """A `PolygonStep` that takes the `problem_lib.h` template from `templates_dir`.

    `StepContext` doesn't carry `templates_dir` — the other polygon steps don't read files
    from `templates/` — so the directory is fixed when building the
    step. `STEP` below is built with `TEMPLATES_DIR`; another directory is needed only by
    tests.
    """
    templates_dir = Path(templates_dir)

    def execute(ctx: StepContext, client: PolygonClient) -> StepResult:
        """Uploads the problem's `problem_lib.h` as is (`ctx.spec` isn't
        used) if it differs from the sync base."""
        polygon_id = require_polygon_state(ctx).polygon_id
        try:
            path = ensure_problem_lib(
                ctx.problem_id, problems_dir=ctx.problems_dir, templates_dir=templates_dir
            )
        except FileNotFoundError as exc:
            raise PolygonStepError(str(exc)) from exc
        content = path.read_bytes()
        items = {PROBLEM_LIB_KEY: digest(content)}
        if not changed_items(ctx, items):
            return StepResult(NO_CHANGES_MESSAGE)

        client.call(
            "problem.saveFile",
            {
                "problemId": str(polygon_id),
                "type": "resource",
                "name": PROBLEM_LIB_FILENAME,
                "file": content,
            },
        )
        return StepResult(
            f"uploaded {PROBLEM_LIB_FILENAME} as resource to Polygon id={polygon_id}", items
        )

    def compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
        """By the sync base (`sync_status`). While the problem has no copy,
        the template is compared — push copies and uploads it."""

        def read_items() -> dict[str, str]:
            path = problem_lib_path(problem_id, problems_dir)
            if not path.exists():
                path = template_problem_lib_path(templates_dir)
            if not path.exists():
                raise PolygonStepError(f"{path} not found")
            return {PROBLEM_LIB_KEY: digest(path.read_bytes())}

        return sync_status(
            problem_id, problems_dir, key_prefix=PROBLEM_LIB_KEY, read_items=read_items
        )

    return PolygonStep(
        name=STEP_NAME,
        check_done=never_done,
        execute=execute,
        compute_status=compute_status,
    )


STEP = make_step()
