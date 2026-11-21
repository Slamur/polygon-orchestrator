"""A Polygon step — uploading the problem's files needed only by the orchestrator and the
author to Polygon as aux files (`problem.saveFile`, `type=aux` — the
"Attachments" section on the Files page) — the `authoring/` files of the problem directory, on
Polygon — by file name, without the directory:

- `spec.yaml` — the problem's spec, the input of the generative steps (mandatory);
- `constraints.yaml` — the whole `constraints_pick` result (ranges,
  groups, the `validator` section), as the step wrote it; the problem's actual TL/ML
  are transferred by `set_constraints` from `limits.yaml`. A file that previous
  versions of `polygon pull` assembled from only TL/ML (`PULLED_CONSTRAINTS_HEADER`)
  isn't the result of `constraints_pick` and isn't uploaded;
- `tests_design.yaml` — the test design the `tests_plan` model wrote;
- `tests_plan.yaml` — the test plan (the `tests_plan` step, possibly with the author's
  edits) from which the generators and the test-script were written;
- `scoring.yaml` — the constraints cells of the statement's subtasks table;
- `draft<extension>` — a copy of the author's draft from which
  `init --draft` wrote the spec;
- `draft-tutorial.md` — the author's tutorial draft.

The list is `is_authoring_name` (`orchestrator/problem_paths.py`): a new `authoring/`
file is uploaded (and pulled) once it is added there.

All of this is stored on Polygon so that `polygon pull` can restore the problem
in full. The files are uploaded as is, byte by byte (`ctx.spec` — the already parsed
spec — isn't used: the author's comments and formatting must
be preserved). There's no generative step dependency — only `create_problem`.
A file deleted locally stays on Polygon — the step doesn't delete it.

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
from orchestrator.polygon.sync import (
    CONSTRAINTS_KEY,
    PULLED_CONSTRAINTS_HEADER,
    SPEC_KEY,
    digest,
)
from orchestrator.problem_paths import (
    AUTHORING_FILENAMES,
    DRAFT_STEM,
    authoring_dir,
    is_authoring_name,
)

STEP_NAME = "upload_attachments"


def read_attachments(problem_id: str, problems_dir: Path) -> list[tuple[str, bytes]]:
    """[(name, contents), ...]: the spec first, then every other `authoring/` file
    (`is_authoring_name`) alphabetically — except a `constraints.yaml` that isn't the
    result of `constraints_pick`. The name is both the name of the aux file on Polygon
    and the sync base key. No spec — `PolygonStepError`."""
    directory = authoring_dir(Path(problems_dir) / problem_id)
    spec_path = directory / SPEC_KEY
    if not spec_path.exists():
        raise PolygonStepError(f"{spec_path} not found")
    attachments = [(SPEC_KEY, spec_path.read_bytes())]

    for path in sorted(directory.iterdir()):
        name = path.name
        if name == SPEC_KEY or not path.is_file() or not is_authoring_name(name):
            continue
        content = path.read_bytes()
        if name == CONSTRAINTS_KEY and content.startswith(
            PULLED_CONSTRAINTS_HEADER.encode("utf-8")
        ):
            continue
        attachments.append((name, content))
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
        key_prefix=(*AUTHORING_FILENAMES, DRAFT_STEM),
        read_items=lambda: _items(read_attachments(problem_id, problems_dir)),
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=never_done,
    execute=_execute,
    compute_status=_compute_status,
)
