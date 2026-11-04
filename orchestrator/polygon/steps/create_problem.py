"""Polygon step 1 — creating the problem in Polygon by the spec's `problem_id`,
CLAUDE.md, "Scope (current)" (creating the problem on Polygon).

For a created problem the sync base is known — it's empty — so
its revision is recorded, and push continues uploading right away. A problem
found on Polygon by name is bound without a revision: what's in it is
unknown, and push stops until `polygon pull`.

Run via `orchestrator.polygon.steps.base.run_step(STEP, ...)`.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
)
from orchestrator.polygon.steps.base import (
    STATUS_DONE,
    STATUS_NOT_RUN,
    PolygonStep,
    StepContext,
    StepResult,
)
from orchestrator.polygon.sync import fetch_revision
from orchestrator.problem_paths import problem_address, problem_id_for

STEP_NAME = "create_problem"

# The Polygon API doesn't set the "auto-update" checkbox for testlib.h and the standard checkers
# (there's neither a parameter nor a method, see `docs/POLYGON_API.md`), so
# for a just created problem we only remind about it. We don't remind for a problem
# found by name: someone has already set it up.
_AUTO_UPDATE_REMINDER = (
    'REMINDER: enable "auto-update" for testlib.h and the standard checker '
    "in the Polygon UI — the API cannot set it"
)


def _check_done(ctx: StepContext) -> str | None:
    """The local state already exists — the problem is bound, no network needed."""
    existing = load_polygon_state(ctx.problem_id, problems_dir=ctx.problems_dir)
    if existing is None:
        return None
    return f"already linked to Polygon id={existing.polygon_id}, skipped"


def _compute_status(problem_id: str, problems_dir: Path) -> tuple[str, str]:
    """Done if there's a binding to Polygon (`polygon_state.json`)."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        return STATUS_NOT_RUN, ""
    return STATUS_DONE, f"polygon_id={state.polygon_id}"


def _execute(ctx: StepContext, client: PolygonClient) -> StepResult:
    """Reuses an existing problem on Polygon with this name, otherwise creates a new one.
    The problem's name on Polygon is its `problem_id` (`problem_id_for`)."""
    name = problem_id_for(ctx.problems_dir, ctx.problem_id)
    polygon_id = _find_polygon_id_by_name(client, name)
    if polygon_id is not None:
        _link(ctx, polygon_id, revision=None)
        address = problem_address(ctx.problems_dir, ctx.problem_id)
        message = (
            f"found existing Polygon problem by name (id={polygon_id}), reused — run "
            f"`orchestrator {address} polygon pull` before pushing to it"
        )
        return StepResult(message, notice=message)

    polygon_id = _create_polygon_problem(client, name)
    _link(ctx, polygon_id, revision=fetch_revision(client, polygon_id).revision)
    message = f"created new Polygon problem (id={polygon_id}); {_AUTO_UPDATE_REMINDER}"
    return StepResult(message, notice=message)


def _find_polygon_id_by_name(client: PolygonClient, name: str) -> int | None:
    """The id of the problem on Polygon named `name`, or `None` if there's no such one.

    NOTE: we assume that the `name` filter in `problems.list` is an exact
    match (or an empty list), not a substring search. This isn't verified on the real
    API yet: if it turns out fuzzy, the result needs to be
    additionally filtered by `item["name"] == name`.
    """
    found = client.call("problems.list", {"name": name})
    return found[0]["id"] if found else None


def _create_polygon_problem(client: PolygonClient, name: str) -> int:
    """Creates the problem on Polygon and returns its id."""
    return client.call("problem.create", {"name": name})["id"]


def _link(ctx: StepContext, polygon_id: int, *, revision: int | None) -> None:
    """Saves the `problem_id` -> Polygon problemId binding in `polygon_state.json`."""
    save_polygon_state(
        ctx.problem_id,
        PolygonState(
            problem_id=problem_id_for(ctx.problems_dir, ctx.problem_id),
            polygon_id=polygon_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            revision=revision,
        ),
        problems_dir=ctx.problems_dir,
    )


STEP = PolygonStep(
    name=STEP_NAME,
    check_done=_check_done,
    execute=_execute,
    compute_status=_compute_status,
    # It only creates the problem/binds to an existing one — there's nothing to commit
    # in the working copy.
    commits_changes=False,
)
