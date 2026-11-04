"""Running the polygon steps for one `problem_id` (CLAUDE.md, "Scope
(current)": creating the problem on Polygon and the following integration steps).

Each step is always called, but uploads only the problem elements
changed since the last sync (the base — `revision`/`synced` in
`polygon_state.json`, see `orchestrator/polygon/sync.py`). Like
`git push` with a fast-forward check: before the first uploading step
the problem's revision on Polygon is compared (`_sync_baseline`):

- the revision equals the recorded one and there are no uncommitted edits in the working copy —
  the base can be trusted, the steps upload only what changed;
- the working copy has uncommitted edits — push always stops,
  even with `force`: otherwise the commit after a step would carry them into the revision;
- `force` is given — the base isn't used, the steps upload everything (like `git push
  --force`), and the first commit replaces the base entirely with what was uploaded: the previous
  records of what's on Polygon could have become outdated after edits bypassing the orchestrator,
  and the revision in the base becomes the current one;
- the revision is unknown (the problem is bound to one already existing on Polygon or
  from before this field appeared) — what's on Polygon is unknown, push
  stops until `polygon pull` (or `--force`);
- the revision is different — Polygon was changed bypassing the orchestrator, push
  stops suggesting `polygon pull` or `polygon push
  --force`.

After each step that uploaded something (except those with
`commits_changes=False`), the problem's working copy is committed on Polygon
(`problem.commitChanges`), and only after that what was uploaded and the new
revision are recorded in the base — so that on a failure at a later step everything
done by the previous ones is already in the revision and in the base.

At the end of the run (and on a step failure) — the version stamps of the files on Polygon
(`stamps`, see `orchestrator/polygon/sync.py`), so that the next `polygon
pull` doesn't download what was uploaded: `_record_stamps`, if something was committed
or files from the base have no stamps. Stamps are written only if the revision on
Polygon after the listing is still the one in the base, and there are no uncommitted edits —
otherwise the listing could have captured someone else's edit. An error here doesn't fail
the push: without stamps the next pull just downloads these files.

`no_commit` (`polygon push --no-commit`) — what was uploaded stays in the working
copy on Polygon, the author sorts it out there and commits themselves, in one batch.
There are no commits, so uncommitted edits don't stop push (you can
upload one step at a time), only a warning: the upload will overwrite the same
files of the working copy. The base and the stamps don't change: Polygon has no revision
with the uploaded files yet. After the commit on Polygon the revision becomes different, and
a normal push requires `polygon pull` — it sees the same files as locally
and updates the base.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.cache import PROBLEMS_DIR
from orchestrator.polygon.client import PolygonApiError, PolygonClient
from orchestrator.polygon.state import load_polygon_state, record_stamps, update_sync
from orchestrator.polygon.steps import (
    create_problem,
    set_constraints,
    upload_attachments,
    upload_checker,
    upload_generators,
    upload_interactor,
    upload_problem_lib,
    upload_solutions,
    upload_statement,
    upload_test_script,
    upload_validator,
)
from orchestrator.polygon.steps.base import PolygonStep, PolygonStepError, run_step
from orchestrator.polygon.sync import (
    INTERACTOR_KEY,
    fetch_remote_stamps,
    fetch_revision,
    is_stamped_key,
)
from orchestrator.problem_paths import problem_address
from orchestrator.spec import SpecValidationError, load_problem_spec
from orchestrator.step_order import POLYGON_STEP_ORDER

logger = logging.getLogger(__name__)

_POLYGON_STEPS: dict[str, PolygonStep] = {
    create_problem.STEP_NAME: create_problem.STEP,
    upload_attachments.STEP_NAME: upload_attachments.STEP,
    set_constraints.STEP_NAME: set_constraints.STEP,
    upload_problem_lib.STEP_NAME: upload_problem_lib.STEP,
    upload_validator.STEP_NAME: upload_validator.STEP,
    upload_checker.STEP_NAME: upload_checker.STEP,
    upload_interactor.STEP_NAME: upload_interactor.STEP,
    upload_statement.STEP_NAME: upload_statement.STEP,
    upload_generators.STEP_NAME: upload_generators.STEP,
    upload_solutions.STEP_NAME: upload_solutions.STEP,
    upload_test_script.STEP_NAME: upload_test_script.STEP,
}

# PolygonStepOutcome.status statuses:
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"

_COMMIT_MESSAGE_PREFIX = "orchestrator: "


@dataclass
class PolygonStepOutcome:
    """What happened to one polygon step (or its state for `status`)."""

    step_name: str
    status: str
    detail: str = ""
    # `detail` — the orchestrator's own text (`PolygonStepError`): short,
    # one-line, with a hint at the end — the CLI prints it in full rather than
    # truncating it, like Polygon API responses and unexpected exceptions.
    full_detail: bool = False
    # What the step uploaded (base keys, see `sync.describe_keys`).
    uploaded: tuple[str, ...] = ()
    notice: str = ""  # `StepResult.notice`


@dataclass
class PolygonPipelineResult:
    """The result of running the polygon steps for one `problem_id`."""

    problem_id: str
    outcomes: list[PolygonStepOutcome] = field(default_factory=list)
    spec_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.spec_error is None and all(o.status != OUTCOME_ERROR for o in self.outcomes)


def run_polygon_pipeline(
    problem_id: str,
    *,
    only_step: str | None = None,
    force: bool = False,
    no_commit: bool = False,
    problems_dir: Path = PROBLEMS_DIR,
    client: PolygonClient | None = None,
    on_outcome: Callable[[PolygonStepOutcome], None] | None = None,
    on_step_start: Callable[[str], None] | None = None,
) -> PolygonPipelineResult:
    """Runs the polygon steps for `problem_id` in the `POLYGON_STEP_ORDER` order
    (or only one, if `only_step` is given).

    Each step is always called; what to upload the step decides itself by the sync
    base (see the module docstring). `force` — upload everything, comparing
    neither with the base nor with the revision on Polygon (uncommitted edits
    on Polygon stop push even with `force`). `no_commit` — only
    upload, without commits and writing to the base (see the module docstring).

    After each step with `commits_changes=True` that uploaded something —
    `problem.commitChanges` and writing the base (see `_commit_and_record`). A commit
    error is the same kind of step error: the step gets the "error" status, the pipeline
    stops. What was uploaded but not committed stays in the Polygon working
    copy — the next push sees uncommitted edits and
    stops (`--force` is needed).

    `client` is created once here and passed to all steps — so as not to
    spawn a separate `PolygonClient` with lazy `.env` reading per step.

    It stops at the first step error without raising it outward:
    `result.outcomes` gets an entry with the "error" status, the following steps
    don't run. Besides the expected `PolygonApiError` (a Polygon API error)
    and `PolygonStepError` (the step's local data/dependencies are missing),
    any other exception (network, missing keys,
    a bug in a step) is handled the same way — with a traceback to the log, `detail` then being the type and text of
    the exception. The full text of the error is always written to the log (ERROR), since
    the calling code may show `detail` abbreviated.

    `on_step_start` is called with the step's name before running it, `on_outcome`
    — right after each step (both successful and failed) — so that the CLI
    shows progress as it goes, not as one chunk at the end. A spec validation error — as in `run_pipeline`:
    it's recorded in `result.spec_error`, the client isn't created then.
    """
    try:
        spec = load_problem_spec(problem_id, problems_dir=problems_dir)
    except SpecValidationError as exc:
        return PolygonPipelineResult(problem_id=problem_id, spec_error=str(exc))

    client = client or PolygonClient()
    result = PolygonPipelineResult(problem_id=problem_id)
    steps_to_run = POLYGON_STEP_ORDER if only_step is None else [only_step]

    def record(outcome: PolygonStepOutcome) -> None:
        result.outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)

    baseline_checked = False
    committed = False
    # `force`: the first commit replaces the base entirely (see the module docstring).
    replace_base = force
    synced: dict[str, str] | None = None

    try:
        for step_name in steps_to_run:
            step = _POLYGON_STEPS[step_name]
            logger.info("[%s] step started", step_name)
            if on_step_start is not None:
                on_step_start(step_name)
            try:
                if step.commits_changes and not baseline_checked:
                    synced = _sync_baseline(
                        problem_id,
                        force=force,
                        no_commit=no_commit,
                        problems_dir=problems_dir,
                        client=client,
                    )
                    baseline_checked = True
                step_result = run_step(
                    step, problem_id, spec, problems_dir=problems_dir, client=client, synced=synced
                )
                detail = step_result.message
                uploaded: tuple[str, ...] = ()
                if step.commits_changes and step_result.uploaded:
                    if no_commit:
                        detail += "; not committed"
                    else:
                        detail += "; " + _commit_and_record(
                            problem_id,
                            step_name,
                            step_result.uploaded,
                            replace=replace_base,
                            problems_dir=problems_dir,
                            client=client,
                        )
                        committed = True
                        replace_base = False
                    uploaded = tuple(step_result.uploaded)
            except (PolygonApiError, PolygonStepError) as exc:
                logger.error("[%s] %s", step_name, exc)
                record(
                    PolygonStepOutcome(
                        step_name,
                        OUTCOME_ERROR,
                        str(exc),
                        full_detail=isinstance(exc, PolygonStepError),
                    )
                )
                return result
            except Exception as exc:  # noqa: BLE001 — traceback to the log, not the console
                logger.exception("[%s] unexpected error", step_name)
                record(PolygonStepOutcome(step_name, OUTCOME_ERROR, f"{type(exc).__name__}: {exc}"))
                return result
            record(
                PolygonStepOutcome(
                    step_name,
                    OUTCOME_OK,
                    detail,
                    uploaded=uploaded,
                    notice=step_result.notice,
                )
            )
    finally:
        if baseline_checked and not no_commit:
            _record_stamps(
                problem_id, committed=committed, problems_dir=problems_dir, client=client
            )

    return result


def _sync_baseline(
    problem_id: str, *, force: bool, no_commit: bool, problems_dir: Path, client: PolygonClient
) -> dict[str, str] | None:
    """The sync base for the steps of this run (see the module docstring);
    `None` — upload everything.

    The problem isn't bound yet — `None` without the network: the step itself fails pointing to
    running `create_problem`. Polygon was changed bypassing the orchestrator —
    `PolygonStepError`.

    Uncommitted edits in the working copy on Polygon always stop push,
    including with `force`: the commit after a step commits the working copy
    entirely and would carry someone else's edits into the orchestrator's revision (and the edits in
    overwritten files would be lost without a trace in the history). The exception is
    `no_commit`: there won't be any commits.
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        return None

    address = problem_address(problems_dir, problem_id)
    current = fetch_revision(client, state.polygon_id)
    if current.modified and no_commit:
        logger.warning(
            "Polygon problem id=%s has uncommitted changes — files uploaded now overwrite "
            "them in its working copy",
            state.polygon_id,
        )
    elif current.modified:
        raise PolygonStepError(
            f"'{problem_id}': Polygon problem id={state.polygon_id} has uncommitted changes "
            "in its working copy — commit or discard them on Polygon first (push refuses "
            "to commit them, even with --force), then run "
            f"`orchestrator {address} polygon pull [--overwrite]`"
        )
    if force:
        logger.info("--force: uploading everything, the sync baseline is ignored")
        return None
    if state.revision is None:
        raise PolygonStepError(
            f"'{problem_id}': the Polygon revision of the last sync is unknown (the problem "
            "was linked to an existing Polygon problem or before revisions were recorded) — "
            f"run `orchestrator {address} polygon pull [--overwrite]` first, or overwrite "
            f"Polygon with `orchestrator {address} polygon push --force`"
        )
    if current.revision != state.revision:
        raise PolygonStepError(
            f"'{problem_id}': Polygon problem id={state.polygon_id} changed since the last "
            f"sync (revision {current.revision} on Polygon, {state.revision} recorded) — "
            f"run `orchestrator {address} polygon pull [--overwrite]`, or overwrite "
            f"Polygon with `orchestrator {address} polygon push --force`"
        )
    logger.info("Polygon revision %s matches the last sync", current.revision)
    return dict(state.synced)


def _commit_and_record(
    problem_id: str,
    step_name: str,
    uploaded: dict[str, str],
    *,
    replace: bool = False,
    problems_dir: Path,
    client: PolygonClient,
) -> str:
    """Commits the problem's working copy on Polygon after the step `step_name` and
    records in the sync base what the step uploaded and the new revision.

    `minorChanges=true` — without email notifications: a commit comes after each
    step, otherwise one run would send a letter per step.

    The new revision is reread from Polygon (`problems.list`), not
    computed as "+1" — so the base doesn't depend on how Polygon
    numbers revisions.

    `replace` — the base is replaced entirely with what was uploaded (the first commit of
    `--force`, see the module docstring).
    """
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None:
        raise PolygonStepError(
            f"'{problem_id}': nothing to commit after {step_name} — the problem is not "
            "linked to Polygon (polygon_state.json is missing or corrupted)"
        )
    client.call(
        "problem.commitChanges",
        {
            "problemId": str(state.polygon_id),
            "minorChanges": "true",
            "message": _COMMIT_MESSAGE_PREFIX + step_name,
        },
    )
    revision = fetch_revision(client, state.polygon_id).revision
    update_sync(
        problem_id, synced=uploaded, revision=revision, replace=replace, problems_dir=problems_dir
    )
    return f"committed on Polygon id={state.polygon_id} (revision {revision})"


def _record_stamps(
    problem_id: str, *, committed: bool, problems_dir: Path, client: PolygonClient
) -> None:
    """Records in the base the version stamps of the files on Polygon (see the module
    docstring). Without commits in this run — only if some files from the
    base have no stamps (the problem was synced before they appeared): otherwise
    the listing wouldn't add anything."""
    state = load_polygon_state(problem_id, problems_dir=problems_dir)
    if state is None or state.revision is None:
        return
    unstamped = [key for key in state.synced if is_stamped_key(key) and key not in state.stamps]
    if not committed and not unstamped:
        return
    try:
        # An interactor in the base — the problem is interactive (there's no spec here).
        stamps = fetch_remote_stamps(
            client, state.polygon_id, interactive=state.synced.get(INTERACTOR_KEY) is not None
        )
        current = fetch_revision(client, state.polygon_id)
    except Exception as exc:  # noqa: BLE001 — an optimization, doesn't fail the push
        logger.warning("modification times are not recorded: %s", exc)
        return
    if current.modified or current.revision != state.revision:
        logger.warning(
            "modification times are not recorded: Polygon changed during the push "
            "(revision %s, %s recorded%s) — the next pull will download the files",
            current.revision,
            state.revision,
            ", uncommitted changes" if current.modified else "",
        )
        return
    record_stamps(problem_id, stamps, problems_dir=problems_dir)
    logger.info("recorded modification times of %d Polygon files", len(stamps))


def compute_polygon_step_statuses(
    problem_id: str, *, problems_dir: Path = PROBLEMS_DIR
) -> list[PolygonStepOutcome]:
    """The state of the polygon steps for `orchestrator <problem> polygon status`, without the network:
    by each step's `compute_status`, in the `POLYGON_STEP_ORDER` order.

    The state is only by local data (the sync base in
    `polygon_state.json` and the step's input files); manual edits on the
    Polygon side aren't visible from here.
    """
    outcomes = []
    for step_name in POLYGON_STEP_ORDER:
        status, detail = _POLYGON_STEPS[step_name].compute_status(problem_id, Path(problems_dir))
        outcomes.append(PolygonStepOutcome(step_name, status, detail))
    return outcomes
