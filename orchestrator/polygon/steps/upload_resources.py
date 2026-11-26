"""Polygon step 3 — uploading the problem's resource files `problems/<problem_id>/resources/*`
to Polygon (`problem.saveFile`, `type=resource`).

Each problem has its own copy of `problem_lib.h` (see `PROBLEM_LIB_FILENAME` in
`orchestrator/steps/base.py`): if it doesn't exist yet, the step first copies
`templates/problem_lib.h` there. The other resources (`testlib.h`) are uploaded only if the
problem has its own version of them (put by hand or by `polygon pull`, see
`orchestrator/resources.py`) — never the defaults of `resources/`: Polygon keeps its own
`testlib.h` up to date. A problem whose statement uses `\\Extra` first gets its own
`extra.tex` and `problem.tex` (`orchestrator/statement_extra.py`). As for every file, only
what changed since the sync base: a version written by pull equals the base and isn't
uploaded back.
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
from orchestrator.polygon.sync import PROBLEM_LIB_KEY, RESOURCE_KEY_PREFIX, digest, resource_key
from orchestrator.problem_paths import is_removed_mark
from orchestrator.resources import problem_resources_dir
from orchestrator.statement_extra import ensure_extra_resources, extra_resources
from orchestrator.steps.base import (
    PROBLEM_LIB_FILENAME,
    TEMPLATES_DIR,
    ensure_problem_lib,
    problem_lib_path,
    template_problem_lib_path,
)

STEP_NAME = "upload_resources"


def make_step(templates_dir: Path = TEMPLATES_DIR) -> PolygonStep:
    """A `PolygonStep` that takes the `problem_lib.h` and `extra.tex` templates from
    `templates_dir`.

    `StepContext` doesn't carry `templates_dir` — the other polygon steps don't read files
    from `templates/` — so the directory is fixed when building the
    step. `STEP` below is built with `TEMPLATES_DIR`; another directory is needed only by
    tests.
    """
    templates_dir = Path(templates_dir)

    def execute(ctx: StepContext, client: PolygonClient) -> StepResult:
        """Uploads the problem's resources as is (`ctx.spec` isn't used) that differ
        from the sync base."""
        polygon_id = require_polygon_state(ctx).polygon_id
        try:
            ensure_problem_lib(
                ctx.problem_id, problems_dir=ctx.problems_dir, templates_dir=templates_dir
            )
            ensure_extra_resources(ctx.problems_dir / ctx.problem_id, templates_dir=templates_dir)
        except FileNotFoundError as exc:
            raise PolygonStepError(str(exc)) from exc
        contents = _own_resources(ctx.problems_dir / ctx.problem_id)
        items = {resource_key(name): digest(content) for name, content in contents.items()}
        changed = changed_items(ctx, items)
        if not changed:
            return StepResult(NO_CHANGES_MESSAGE)

        uploaded = []
        for name, content in contents.items():
            if resource_key(name) not in changed:
                continue
            client.call(
                "problem.saveFile",
                {"problemId": str(polygon_id), "type": "resource", "name": name, "file": content},
            )
            uploaded.append(name)
        return StepResult(
            f"uploaded {', '.join(uploaded)} as resource to Polygon id={polygon_id}", changed
        )

    def compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
        """By the sync base (`sync_status`). While the problem has no copy of
        `problem_lib.h`, the template is compared — push copies and uploads it; the same
        for the files `\\Extra` needs (`extra_resources`)."""

        def read_items() -> dict[str, str]:
            problem_dir = Path(problems_dir) / problem_id
            contents = _own_resources(problem_dir)
            try:
                contents.update(extra_resources(problem_dir, templates_dir=templates_dir))
            except FileNotFoundError as exc:
                raise PolygonStepError(str(exc)) from exc
            if PROBLEM_LIB_FILENAME not in contents:
                path = template_problem_lib_path(templates_dir)
                if not path.exists():
                    raise PolygonStepError(
                        f"{problem_lib_path(problem_id, problems_dir)} not found"
                    )
                contents[PROBLEM_LIB_FILENAME] = path.read_bytes()
            return {resource_key(name): digest(content) for name, content in contents.items()}

        return sync_status(
            problem_id,
            problems_dir,
            key_prefix=(PROBLEM_LIB_KEY, RESOURCE_KEY_PREFIX),
            read_items=read_items,
        )

    return PolygonStep(
        name=STEP_NAME,
        check_done=never_done,
        execute=execute,
        compute_status=compute_status,
    )


STEP = make_step()


def _own_resources(problem_dir: Path) -> dict[str, bytes]:
    """The problem's own resource files (name -> contents), sorted by name; the files a
    step marked as removed (`is_removed_mark`) aren't resources."""
    directory = problem_resources_dir(problem_dir)
    if not directory.is_dir():
        return {}
    return {
        path.name: path.read_bytes()
        for path in sorted(directory.iterdir())
        if path.is_file() and not is_removed_mark(path)
    }
