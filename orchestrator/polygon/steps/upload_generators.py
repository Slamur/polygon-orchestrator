"""Polygon step 7 — uploading all `problems/<problem_id>/generators/*.cpp`
(the result of the `generators_draft` generative step; the file names are from the
test plan) to Polygon as source files (`problem.saveFile`,
`type=source`).

`sourceType` isn't passed — as in `upload_validator`, it's assumed that
Polygon determines the language by the `.cpp` extension.

The files are uploaded in alphabetical order of names — for the sake of deterministic
tests and logs; Polygon itself doesn't care about the upload order of source files.
Files with other extensions in `generators/` (e.g. a `.h` that got
there by accident) aren't uploaded.

If a generator was deleted or renamed since the last upload, the old file on
Polygon stays — the step doesn't delete it.

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
from orchestrator.polygon.sync import digest, generator_key
from orchestrator.problem_paths import problem_address

STEP_NAME = "upload_generators"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Uploads each `generators/*.cpp` that differs from the sync
    base (`ctx.spec` isn't used).

    Both dependencies (`create_problem`, `generators_draft`) are checked
    before the first network call.
    """
    polygon_id = require_polygon_state(ctx).polygon_id
    generators = _read_generators(ctx.problem_id, ctx.problems_dir)
    changed = changed_items(ctx, _items(generators))
    if not changed:
        return StepResult(NO_CHANGES_MESSAGE)

    sent = []
    for name, content in generators:
        if generator_key(name) not in changed:
            continue
        client.call(
            "problem.saveFile",
            {"problemId": str(polygon_id), "type": "source", "name": name, "file": content},
        )
        sent.append(name)

    return StepResult(
        f"uploaded {len(sent)} of {len(generators)} generator(s) ({', '.join(sent)}) "
        f"to Polygon id={polygon_id}",
        changed,
    )


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """By the sync base (`sync_status`): a new or changed
    generator makes the step "stale"."""
    return sync_status(
        problem_id,
        problems_dir,
        key_prefix=generator_key(""),
        read_items=lambda: _items(_read_generators(problem_id, problems_dir)),
    )


def _items(generators: list[tuple[str, bytes]]) -> dict[str, str]:
    return {generator_key(name): digest(content) for name, content in generators}


def _read_generators(problem_id: str, problems_dir: Path) -> list[tuple[str, bytes]]:
    """[(name, contents), ...] for `generators/*.cpp` alphabetically by name.
    No directory or not a single `.cpp` in it — `PolygonStepError`."""
    generators_dir = Path(problems_dir) / problem_id / "generators"
    if not generators_dir.is_dir():
        address = problem_address(problems_dir, problem_id)
        raise PolygonStepError(
            f"'{problem_id}': directory {generators_dir} not found — first "
            f"run the generative step generators_draft (`orchestrator {address} llm generate "
            f"--step generators_draft`)"
        )
    paths = sorted(generators_dir.glob("*.cpp"), key=lambda p: p.name)
    if not paths:
        raise PolygonStepError(f"'{problem_id}': no .cpp files in {generators_dir}")
    return [(path.name, path.read_bytes()) for path in paths]


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
