"""The shared logic of the polygon steps: "run if not run yet".

A step (`PolygonStep`) describes only its specifics — how to tell locally
that it's already done (`check_done`), and what to do if not (`execute`).
Everything else — the order of checks, lazily creating `PolygonClient`,
logging the result — is done by `run_step`, the same for all steps.

Steps uploading the problem's contents upload only the elements changed since the
last sync (`changed_items`, the base — `synced` in
`polygon_state.json`, see `orchestrator/polygon/sync.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.cache import PROBLEMS_DIR
from orchestrator.polygon.client import PolygonClient
from orchestrator.polygon.state import PolygonState, load_polygon_state
from orchestrator.problem_paths import problem_address
from orchestrator.spec import ProblemSpec

logger = logging.getLogger(__name__)


# The states of a polygon step for `orchestrator <problem> polygon status` (without the network).
STATUS_DONE = "done"
STATUS_STALE = "stale"
STATUS_NOT_RUN = "not run"

NO_CHANGES_MESSAGE = "no changes since the last sync, skipped"


class PolygonStepError(Exception):
    """The step can't be run because of a missing dependency or
    contradictory local data. Unlike `PolygonApiError` — this is an
    error BEFORE going to the network."""


@dataclass(frozen=True)
class StepContext:
    """Everything the step needs besides the Polygon client.

    `synced` — the sync base (key -> sha256, see `sync.py`) with which
    the step compares the local elements; `None` — the base is unknown or
    `push --force`: upload everything."""

    problem_id: str
    spec: ProblemSpec
    problems_dir: Path
    synced: dict[str, str] | None = None


@dataclass(frozen=True)
class StepResult:
    """The step's outcome: a message and what exactly went to Polygon (key -> sha256).
    An empty `uploaded` — Polygon didn't change: there's nothing to commit or record.

    `message` goes to the log; `notice` — what the CLI shows the author separately
    (e.g. that the problem was created on Polygon), empty — nothing."""

    message: str
    uploaded: dict[str, str] = field(default_factory=dict)
    notice: str = ""


@dataclass(frozen=True)
class PolygonStep:
    """The description of one polygon step.

    `check_done` — only a local check (files in `problems/<id>/`), without
    network calls: returns a human-readable message if the step is already
    done, otherwise `None`.

    `execute` — the real work through the Polygon API; returns `StepResult`.
    It doesn't catch `PolygonApiError` and `PolygonStepError`.

    `compute_status(problem_id, problems_dir)` — only a local check
    for `status`: a pair (one of `STATUS_*`, an explanation). Unlike
    `check_done` it doesn't require a spec and may report "stale" — the local
    files changed since the last sync.

    `commits_changes` — whether to commit the problem's working copy on Polygon after
    a step that uploaded something (see `run_polygon_pipeline`). `False` — for
    steps that don't change the problem's contents (`create_problem`).
    """

    name: str
    check_done: Callable[[StepContext], str | None]
    execute: Callable[[StepContext, PolygonClient], StepResult]
    compute_status: Callable[[str, Path], tuple[str, str]]
    commits_changes: bool = True


def never_done(ctx: StepContext) -> str | None:
    """`check_done` of the steps uploading contents: the step always runs,
    and what to upload it decides itself by `ctx.synced` (`changed_items`)."""
    return None


def require_polygon_state(ctx: StepContext) -> PolygonState:
    """The problem's state from `polygon_state.json` — for all steps after
    `create_problem` (`polygon_id` is taken from there). Without the network; if the problem
    isn't created on Polygon yet — `PolygonStepError` saying which step to
    run first."""
    state = load_polygon_state(ctx.problem_id, problems_dir=ctx.problems_dir)
    if state is None:
        address = problem_address(ctx.problems_dir, ctx.problem_id)
        raise PolygonStepError(
            f"'{ctx.problem_id}': the problem is not created on Polygon yet — first "
            f"run the create_problem step (`orchestrator {address} polygon push "
            "--step create_problem`)"
        )
    return state


def changed_items(ctx: StepContext, items: dict[str, str]) -> dict[str, str]:
    """The elements from `items` (key -> current sha256) that differ from the
    sync base; without a base (`ctx.synced is None`) — all of them."""
    if ctx.synced is None:
        return dict(items)
    return {key: value for key, value in items.items() if ctx.synced.get(key) != value}


def sync_status(
    problem_id: str,
    problems_dir: Path,
    *,
    key_prefix: str | tuple[str, ...],
    read_items: Callable[[], dict[str, str]],
) -> tuple[str, str]:
    """The step's status for `polygon status` by the sync base.

    `key_prefix` — the prefix (or several) of the step's keys in `synced` (whether
    the step has a base at all); `read_items` — the step's current local elements (key ->
    sha256), may raise `PolygonStepError` if there are no files.

    "not run" — there's no base for the step's keys; "stale" — some element
    differs from the base (or the files can no longer be read); "done" — all
    match.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    synced = {
        key: value
        for key, value in (state.synced if state is not None else {}).items()
        if key.startswith(key_prefix)
    }
    try:
        items = read_items()
    except PolygonStepError as exc:
        return (STATUS_STALE if synced else STATUS_NOT_RUN), f"local files are unreadable: {exc}"
    if not synced:
        return STATUS_NOT_RUN, ""
    changed = sorted(key for key, value in items.items() if synced.get(key) != value)
    if changed:
        return STATUS_STALE, "changed since the last sync: " + ", ".join(changed)
    return STATUS_DONE, f"{len(items)} item(s) in sync"


def run_step(
    step: PolygonStep,
    problem_id: str,
    spec: ProblemSpec,
    *,
    problems_dir: Path = PROBLEMS_DIR,
    client: PolygonClient | None = None,
    synced: dict[str, str] | None = None,
) -> StepResult:
    """Runs `step` for `problem_id` if it hasn't run yet.

    If `step.check_done` reports that the step is already done — returns that
    message without creating a client or making network calls. Otherwise
    calls `step.execute`. The message is written to the log: the log goes to
    `problems/<id>/polygon.log`, and only the CLI output of the current run sees
    the returned value.

    `synced` — the sync base (see `StepContext`); `None` — upload
    everything.

    `PolygonApiError` and `PolygonStepError` from `execute` are propagated
    outward — the pipeline stops on the side of `orchestrator/polygon/pipeline.py`.
    """
    ctx = StepContext(
        problem_id=problem_id, spec=spec, problems_dir=Path(problems_dir), synced=synced
    )

    message = step.check_done(ctx)
    if message is not None:
        result = StepResult(message)
    else:
        if client is None:
            client = PolygonClient()
        result = step.execute(ctx, client)

    logger.info("[%s] %s", step.name, result.message)
    return result
