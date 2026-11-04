"""Running the generative steps for one `problem_id`, taking the cache into account (CLAUDE.md,
"Running", "Caching by spec hash"), the cache status of the steps
(`compute_step_statuses`) and accepting the current files without calling the model
(`accept_steps`).

`run_pipeline` doesn't call the model if it isn't needed: before each step
it computes `input_hash`/`prompt_hash` (without running the step itself) and checks
`cache.is_cache_valid(...)` — the model is called only if the cache is invalid,
`force=True` or `fresh=True`. If the step's files already exist on disk, the step
runs in revision mode (`steps.base.StepRevision`): the model gets
them and what changed in the step's input; `fresh=True` — from scratch, without the current
files. If a step returns `status: uncertain`
(`StepUncertainError`), the pipeline stops for this `problem_id`
(the following steps don't run), but doesn't raise the exception outward: it's
an expected outcome, not a failure, and the CLI prints it as the step's result.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from orchestrator import model_router
from orchestrator.cache import (
    UNCERTAIN_STATUS,
    CacheEntry,
    PendingResponse,
    compute_input_values,
    compute_prompt_hash,
    compute_prompt_parts,
    describe_changed_parts,
    discard_pending_response,
    input_parts_from_values,
    is_cache_valid,
    load_cache_entry,
    load_pending_response,
    save_cache_entry,
)
from orchestrator.llm_client import LLMCallError
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import (
    TUTORIAL_DRAFT_FILENAME,
    is_removed_mark,
    problem_address,
)
from orchestrator.references import SpecReferenceError
from orchestrator.spec import SpecValidationError, load_problem_spec
from orchestrator.step_order import (
    LLM_STEPS,
    MECHANICAL_STEPS,
    ON_DEMAND_STEPS,
    STEP_ORDER,
)
from orchestrator.steps import (
    checker_draft,
    constraints_pick,
    generators_draft,
    interactor_draft,
    limits_from_constraints,
    solutions_draft,
    statement_draft,
    test_script_draft,
    tests_plan,
    tutorial_draft,
)
from orchestrator.steps.base import (
    CONFLICTS_REPORT,
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepRevision,
    StepUncertainError,
)

logger = logging.getLogger(__name__)

_STEP_MODULES: dict[str, ModuleType] = {
    "statement_draft": statement_draft,
    "constraints_pick": constraints_pick,
    "limits_from_constraints": limits_from_constraints,
    "tests_plan": tests_plan,
    "generators_draft": generators_draft,
    "test_script_draft": test_script_draft,
    "checker_draft": checker_draft,
    "interactor_draft": interactor_draft,
    "solutions_draft": solutions_draft,
    "tutorial_draft": tutorial_draft,
}

# The steps' artifacts that the following steps get as upstream input (see
# CLAUDE.md, "Caching by spec hash"): source step -> the artifact name
# (the file — the source's `primary_artifact_path`) and step -> which of them it needs.
_UPSTREAM_PRODUCERS: dict[str, str] = {
    "constraints_pick": CONSTRAINTS_ARTIFACT,
    "tests_plan": tests_plan.TESTS_PLAN_ARTIFACT,
}
_STEP_UPSTREAM: dict[str, tuple[str, ...]] = {
    "limits_from_constraints": ("constraints_pick",),
    "tests_plan": ("constraints_pick",),
    "generators_draft": ("constraints_pick", "tests_plan"),
    "test_script_draft": ("constraints_pick", "tests_plan"),
    "solutions_draft": ("constraints_pick",),
}

# StepOutcome.status statuses:
OUTCOME_CACHE_HIT = "cache_hit"
OUTCOME_CONFIRMED = "confirmed"
OUTCOME_PROPOSED = "proposed"
OUTCOME_UNCERTAIN = "uncertain"
OUTCOME_SKIPPED_OPTIONAL = "skipped_optional"
OUTCOME_ERROR = "error"
OUTCOME_CONFLICT = "conflict"
OUTCOME_NO_PENDING = "no_pending_response"


@dataclass
class StepOutcome:
    """What happened to one step within one pipeline run."""

    step_name: str
    status: str
    detail: str = ""
    notes: list[dict[str, Any]] = field(default_factory=list)
    # Revision mode (`steps.base.StepResult`): files marked
    # `.removed`, renames and the directory with the previous versions of files.
    removed: list[tuple[Path, str]] = field(default_factory=list)
    renamed: list[tuple[Path, Path, str]] = field(default_factory=list)
    backup_dir: Path | None = None
    # What of the model's response wasn't applied (file name, reason) and where the
    # model's versions were set aside (`steps.base.StepResult`).
    conflicts: list[tuple[str, str]] = field(default_factory=list)
    conflicts_dir: Path | None = None
    # The step worked in this run (called the model or applied a saved
    # response) rather than being skipped — the `llm_reports/` report is written only about such a step.
    ran: bool = False
    # The written files (artifact name -> path), which of them are new, and
    # the compile-check errors (artifact name -> stderr).
    artifact_paths: dict[str, Path] = field(default_factory=dict)
    created: list[Path] = field(default_factory=list)
    compile_errors: dict[str, str] = field(default_factory=dict)
    # Written source files with non-ASCII characters (artifact name -> message).
    charset_errors: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """The result of running all steps for one `problem_id`."""

    problem_id: str
    outcomes: list[StepOutcome] = field(default_factory=list)
    stopped_uncertain: bool = False
    spec_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.spec_error is None and not self.stopped_uncertain


def _artifact_present(path: Path) -> bool:
    """Checks that the file/directory expected by the step is actually in place.

    For the `solutions_draft` step `path` is a directory with an arbitrary set of
    files (the model names the solutions itself), so "in place" means
    "exists and has something in it besides what's marked `.removed`"; for
    the other steps `path` is a particular file.
    """
    if path.is_dir():
        return any(not is_removed_mark(child) for child in path.iterdir())
    return path.exists()


@dataclass
class _StepHashes:
    """The current hashes of the step's input — what `CacheEntry` is compared with."""

    input_hash: str
    prompt_hash: str
    input_parts: dict[str, str]
    prompt_parts: dict[str, str]
    model: str
    input_values: dict[str, Any]


def _generate_command(step_name: str) -> str:
    """Which command runs the step."""
    if step_name in ON_DEMAND_STEPS:
        return "llm tutorial"
    return f"llm generate --step {step_name}"


def _optional_skip_detail(
    step_name: str, spec: Any, problem_id: str, problems_dir: Path
) -> str | None:
    """Why an optional step isn't needed for this problem; `None` — it is needed."""
    if (
        step_name == "tutorial_draft"
        and not tutorial_draft.draft_path(problem_id, problems_dir=problems_dir).is_file()
    ):
        return (
            f"no authoring/{TUTORIAL_DRAFT_FILENAME} — the tutorial was not requested by the author"
        )
    if step_name == "solutions_draft" and spec.solutions is None:
        return "spec has no 'solutions' section — solution drafts were not requested by the author"
    if step_name == "checker_draft" and spec.checker.custom_needed is False:
        return "checker.custom_needed=false — a standard checker is used, no draft requested"
    if step_name == "interactor_draft" and not spec.interactive:
        return "spec has no 'interactor' section — the problem is not interactive"
    return None


def _load_upstream(
    step_name: str,
    problem_id: str,
    spec: Any,
    upstream_artifacts: dict[str, Any],
    problems_dir: Path,
    *,
    tutorial_refs: list[str] | None = None,
) -> None:
    """Picks up from disk the step's upstream artifacts (`_STEP_UPSTREAM`)
    that aren't in `upstream_artifacts` yet: the source step didn't run in this
    run (only --step or skipped by the cache). For `tutorial_draft` — the
    files of its input (`tutorial_draft.read_inputs`); `tutorial_refs` — the solutions
    from `--ref`, `None` — the same as in the previous run. `ValueError` — the step's input
    isn't on disk."""
    if step_name == "tutorial_draft":
        if tutorial_refs is None:
            pending = load_pending_response(problem_id, step_name, problems_dir=problems_dir)
            entry = (
                pending.cache_entry
                if pending is not None
                else load_cache_entry(problem_id, step_name, problems_dir=problems_dir)
            )
            tutorial_refs = tutorial_draft.remembered_refs(
                entry.input_values if entry is not None else None
            )
        upstream_artifacts[tutorial_draft.INPUTS_KEY] = tutorial_draft.read_inputs(
            problem_id, tutorial_refs, language=spec.language, problems_dir=problems_dir
        )
    for producer in _STEP_UPSTREAM.get(step_name, ()):
        name = _UPSTREAM_PRODUCERS[producer]
        if name in upstream_artifacts:
            continue
        path = _STEP_MODULES[producer].primary_artifact_path(
            problem_id, spec, problems_dir=problems_dir
        )
        if path.exists():
            upstream_artifacts[name] = path.read_text(encoding="utf-8")


def _remember_upstream(step_name: str, path: Path, upstream_artifacts: dict[str, Any]) -> None:
    """After the source step — its artifact, as it is on disk now."""
    if step_name in _UPSTREAM_PRODUCERS and path.exists():
        upstream_artifacts[_UPSTREAM_PRODUCERS[step_name]] = path.read_text(encoding="utf-8")


def _compute_step_hashes(
    problem_id: str,
    step_name: str,
    spec: Any,
    upstream_artifacts: dict[str, Any],
    *,
    prompts_dir: Path,
    templates_dir: Path,
) -> _StepHashes:
    """The step's hashes without calling the model. `ValueError` — a needed
    upstream artifact (`constraints.yaml`) is missing, `SpecReferenceError` —
    a spec reference isn't found or gives the step nothing."""
    module = _STEP_MODULES[step_name]
    input_hash = module.compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*module.hash_inputs(problem_id, spec, upstream_artifacts))
    if step_name in MECHANICAL_STEPS:
        return _StepHashes(
            input_hash=input_hash,
            prompt_hash=module.PROMPT_HASH,
            input_parts=input_parts_from_values(input_values),
            prompt_parts={},
            model=module.MODEL,
            input_values=input_values,
        )
    extra_documents = module.extra_context_documents(spec, templates_dir=templates_dir)
    prompt_kwargs = dict(
        prompts_dir=prompts_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_documents,
    )
    return _StepHashes(
        input_hash=input_hash,
        prompt_hash=compute_prompt_hash(step_name, **prompt_kwargs),
        input_parts=input_parts_from_values(input_values),
        prompt_parts=compute_prompt_parts(step_name, **prompt_kwargs),
        model=model_router.STEP_TO_MODEL[step_name],
        input_values=input_values,
    )


def _stale_reason(entry: CacheEntry, hashes: _StepHashes, *, brief: bool = False) -> str:
    """Why the step's cache is invalid (given it's already known to be invalid and
    `entry.status` isn't `uncertain`): what exactly changed — by the hashes of the
    parts, if they are in the cache entry. `brief` — only what changed in
    general (model, spec, prompt), without the list of parts — for the console."""
    reasons: list[str] = []
    parts_unknown = False
    if entry.model != hashes.model:
        reasons.append(f"model changed ({entry.model} -> {hashes.model})")
    for summary, old_hash, new_hash, old_parts, new_parts in (
        (
            "spec (or upstream artifact) changed",
            entry.input_hash,
            hashes.input_hash,
            entry.input_parts,
            hashes.input_parts,
        ),
        (
            "prompt template changed",
            entry.prompt_hash,
            hashes.prompt_hash,
            entry.prompt_parts,
            hashes.prompt_parts,
        ),
    ):
        if old_hash == new_hash:
            continue
        if brief:
            reasons.append(summary)
            continue
        if old_parts is None:
            parts_unknown = True
            reasons.append(summary)
            continue
        changed = describe_changed_parts(old_parts, new_parts)
        reasons.append(f"{summary}: {', '.join(changed)}" if changed else summary)
    reason = "; ".join(reasons) or "cache entry does not match"
    if parts_unknown:
        reason += " (which parts — unknown: the cache entry predates per-part hashes)"
    return reason


def _revision_for_step(
    module: ModuleType,
    problem_id: str,
    spec: Any,
    cache_entry: CacheEntry | None,
    hashes: _StepHashes,
    problems_dir: Path,
) -> StepRevision | None:
    """Revision mode if the step's files exist on disk; otherwise `None` (from scratch).
    What changed in the input — by the `input_values` of the cache entry; a change of the model and
    the prompts/documents — only by name (`other_changes`)."""
    current = module.current_artifacts(problem_id, spec, problems_dir=problems_dir)
    if not current:
        return None
    other_changes: list[str] = []
    if cache_entry is not None:
        if cache_entry.model != hashes.model:
            other_changes.append(f"model: {cache_entry.model} -> {hashes.model}")
        if cache_entry.prompt_hash != hashes.prompt_hash:
            changed = describe_changed_parts(cache_entry.prompt_parts, hashes.prompt_parts)
            other_changes.extend(changed or ["prompt templates or attached documents"])
    return StepRevision(
        current_paths=current,
        previous_input_values=cache_entry.input_values if cache_entry is not None else None,
        other_changes=other_changes,
    )


def _pending_revision(
    pending: PendingResponse,
    module: ModuleType,
    problem_id: str,
    spec: Any,
    cache_entry: CacheEntry | None,
    hashes: _StepHashes,
    problems_dir: Path,
) -> tuple[StepRevision | None, str | None]:
    """The mode in which a saved response is applied (`_revision_for_step`,
    if the response was received in revision mode), or why it can't be applied
    to the step's current input: `(revision, None)` or `(None, reason)`."""
    try:
        ModelResponse(**pending.response)
    except TypeError as exc:
        return None, f"the saved response is malformed ({exc})"
    entry = pending.cache_entry
    if not is_cache_valid(entry, hashes.input_hash, hashes.prompt_hash, hashes.model):
        return None, (
            "the saved response was received for a different input of the step "
            f"({_stale_reason(entry, hashes)})"
        )
    if not pending.revised:
        return None, None
    revision = _revision_for_step(module, problem_id, spec, cache_entry, hashes, problems_dir)
    if revision is None:
        return None, (
            "the saved response revises the current files of the step, "
            "but there are none on disk now"
        )
    return revision, None


def _run_step_into(
    result: PipelineResult,
    module: ModuleType,
    step_name: str,
    problem_id: str,
    spec: Any,
    upstream_artifacts: dict[str, Any],
    *,
    revision: StepRevision | None,
    saved_response: ModelResponse | None,
    detail: str,
    prompts_dir: Path,
    problems_dir: Path,
    templates_dir: Path,
) -> bool:
    """Runs the step and appends its outcome to `result`. `False` — the pipeline
    is stopped (`uncertain` or the model's response failed the check)."""
    try:
        step_result = module.run_step(
            problem_id,
            spec,
            dict(upstream_artifacts) or None,
            revision=revision,
            saved_response=saved_response,
            prompts_dir=prompts_dir,
            problems_dir=problems_dir,
            templates_dir=templates_dir,
        )
    except StepUncertainError as exc:
        # The text of the notes is in the step report (`llm_report`), only the fields here.
        fields = ", ".join(str(note.get("field", "?")) for note in exc.notes)
        detail = (
            f"the model needs more data in the spec ({fields or 'no fields named'}) — "
            f"fix {problem_address(problems_dir, problem_id)}/authoring/spec.yaml "
            "and run the step again"
        )
        result.outcomes.append(
            StepOutcome(step_name, OUTCOME_UNCERTAIN, detail, notes=exc.notes, ran=True)
        )
        result.stopped_uncertain = True
        return False
    except LLMCallError as exc:
        # The model didn't respond (a timeout, a CLI/API error) — there's nothing to record,
        # the next run calls it again.
        message = f"the model call failed: {exc} — nothing written"
        if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
            message += (
                "; the earlier model response is saved, "
                f"'orchestrator {problem_address(problems_dir, problem_id)} "
                f"llm apply --step {step_name}' applies it without calling the model"
            )
        result.outcomes.append(StepOutcome(step_name, OUTCOME_ERROR, message, ran=True))
        result.stopped_uncertain = True
        return False
    except ValueError as exc:
        # The model's response failed the check — nothing is written to `problems/`.
        # The response itself is saved (`PendingResponse`): after fixing the cause it
        # can be applied without a new model call.
        message = str(exc)
        if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
            message += (
                " — nothing written; the model response is saved, "
                f"'orchestrator {problem_address(problems_dir, problem_id)} "
                f"llm apply --step {step_name}' applies it without calling the model"
            )
        result.outcomes.append(StepOutcome(step_name, OUTCOME_ERROR, message, ran=True))
        result.stopped_uncertain = True
        return False

    conflicts_dir = step_result.conflicts_dir
    if conflicts_dir is not None:
        address = problem_address(problems_dir, problem_id)
        if step_result.not_applied_reason is not None:
            what = (
                f"{step_result.not_applied_reason} — nothing of the response is applied "
                f"(status: {step_result.status})"
            )
        else:
            what = (
                f"{len(step_result.conflicts)} part(s) of the response not applied "
                f"(status: {step_result.status})"
            )
        resolve = (
            f"{what} — see {conflicts_dir / CONFLICTS_REPORT}; "
            "resolve by hand, delete the directory and run "
            f"'orchestrator {address} llm accept --step {step_name}'"
        )
        if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
            resolve += (
                " (the response is saved: after fixing the cause "
                f"'orchestrator {address} llm apply --step {step_name}' applies it)"
            )
        detail = f"{detail}; {resolve}" if detail else resolve
    result.outcomes.append(
        StepOutcome(
            step_name,
            OUTCOME_CONFLICT if conflicts_dir is not None else step_result.status,
            detail,
            notes=step_result.notes,
            removed=step_result.removed,
            renamed=step_result.renamed,
            backup_dir=step_result.backup_dir,
            conflicts=step_result.conflicts,
            conflicts_dir=conflicts_dir,
            ran=True,
            artifact_paths=step_result.artifact_paths,
            created=step_result.created,
            compile_errors={
                filename: compiled.stderr
                for filename, compiled in step_result.compile_results.items()
                if not compiled.success
            },
            charset_errors=step_result.charset_errors,
        )
    )
    if conflicts_dir is not None:
        # The following steps depend on this one's files — until the author resolves the
        # conflicts, we don't go further.
        result.stopped_uncertain = True
        return False
    return True


def run_pipeline(
    problem_id: str,
    *,
    force: bool = False,
    fresh: bool = False,
    apply_pending: bool = False,
    only_step: str | None = None,
    tutorial_refs: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> PipelineResult:
    """Runs the steps of `problem_id` in the `STEP_ORDER` order.

    `only_step`, if set, limits the run to one step (used by the
    `--step` CLI flag); the steps before it don't run anyway, but the
    upstream artifacts it needs (`constraints.yaml`) are read from disk —
    it's assumed that the previous steps have already worked (see `cli.py`
    `run <problem_id> --step NAME`, which requires this by hand).

    `force=True` bypasses the cache completely — each needed step calls the model
    again, regardless of `input_hash`/`prompt_hash`. A step whose files
    already exist on disk runs in revision mode (`_revision_for_step`);
    `fresh=True` — from scratch, without the current files, and, like `force`, regardless of the
    cache (a "from scratch" request with a valid cache would otherwise silently do
    nothing). If a step is rerun
    without `force` because of an outdated cache, `StepOutcome.detail` has what exactly
    changed (`_stale_reason`).

    `apply_pending=True` (`llm apply`) — the model isn't called at all: steps with
    a saved unapplied response (`cache.PendingResponse` — a response that
    failed the check) apply it if it was received for the step's current input
    (the same hashes as the cache) and in the same mode (revision or from scratch);
    the other steps are skipped. Only in this mode without `only_step` do
    on-demand steps (`ON_DEMAND_STEPS`) take part: their saved response also
    needs applying, and they can call the model only through `only_step`.

    `tutorial_refs` — the solutions from `--ref` for `tutorial_draft` (`_load_upstream`).

    It stops (without raising an exception) at the first `StepUncertainError` —
    `result.stopped_uncertain=True`, `result.outcomes` contains everything that
    managed to work. Likewise — on a model response that failed the check
    (the step's `ValueError`; the response is saved for `apply_pending`) and on a failed
    model call (`LLMCallError`: a timeout, a CLI/API error). A spec validation error (`SpecValidationError`) isn't
    propagated outward either — it's recorded in `result.spec_error`, and the CLI prints
    it instead of the steps' results.
    """
    try:
        spec = load_problem_spec(problem_id, problems_dir=problems_dir)
    except SpecValidationError as exc:
        return PipelineResult(problem_id=problem_id, spec_error=str(exc))

    result = PipelineResult(problem_id=problem_id)
    upstream_artifacts: dict[str, Any] = {}

    if only_step is not None:
        steps_to_run = [only_step]
    else:
        steps_to_run = LLM_STEPS if apply_pending else STEP_ORDER

    for step_name in steps_to_run:
        module = _STEP_MODULES[step_name]
        logger.info("[%s] step started", step_name)

        skip_detail = _optional_skip_detail(step_name, spec, problem_id, problems_dir)
        if skip_detail is not None:
            result.outcomes.append(StepOutcome(step_name, OUTCOME_SKIPPED_OPTIONAL, skip_detail))
            continue

        pending = None
        if apply_pending:
            pending = load_pending_response(problem_id, step_name, problems_dir=problems_dir)
            if pending is None:
                result.outcomes.append(
                    StepOutcome(
                        step_name, OUTCOME_NO_PENDING, "no saved model response — nothing to apply"
                    )
                )
                continue

        try:
            _load_upstream(
                step_name,
                problem_id,
                spec,
                upstream_artifacts,
                problems_dir,
                tutorial_refs=tutorial_refs,
            )
            hashes = _compute_step_hashes(
                problem_id,
                step_name,
                spec,
                upstream_artifacts,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
            )
        except (ValueError, SpecReferenceError) as exc:
            # ValueError — there's no constraints.yaml (constraints_pick hasn't
            # run successfully yet) or no step input, SpecReferenceError — a spec reference isn't
            # found: the spec or the run needs fixing. It isn't uncertain, but the
            # pipeline stops the same way — with a message, not a traceback.
            result.outcomes.append(StepOutcome(step_name, OUTCOME_ERROR, str(exc)))
            result.stopped_uncertain = True
            return result

        cache_entry = load_cache_entry(problem_id, step_name, problems_dir=problems_dir)
        primary_path = module.primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
        artifact_present = _artifact_present(primary_path)

        if step_name == "tutorial_draft":
            names = tutorial_draft.solution_names(upstream_artifacts[tutorial_draft.INPUTS_KEY])
            logger.info("[%s] solutions as context: %s", step_name, ", ".join(names) or "none")

        if pending is not None:
            revision, problem = _pending_revision(
                pending, module, problem_id, spec, cache_entry, hashes, problems_dir
            )
            if problem is not None:
                result.outcomes.append(
                    StepOutcome(
                        step_name,
                        OUTCOME_ERROR,
                        f"{problem} — run "
                        f"'orchestrator {problem_address(problems_dir, problem_id)} "
                        f"{_generate_command(step_name)}'",
                    )
                )
                result.stopped_uncertain = True
                return result
            if not _run_step_into(
                result,
                module,
                step_name,
                problem_id,
                spec,
                upstream_artifacts,
                revision=revision,
                saved_response=ModelResponse(**pending.response),
                detail="saved model response applied, model not called",
                prompts_dir=prompts_dir,
                problems_dir=problems_dir,
                templates_dir=templates_dir,
            ):
                return result
        elif cache_entry is not None and cache_entry.conflicts is not None and not (force or fresh):
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_CONFLICT,
                    _conflicts_detail(problem_id, step_name, cache_entry, problems_dir)
                    + f" (or '{_generate_command(step_name)} --force' to call the model again)",
                )
            )
            result.stopped_uncertain = True
            return result
        elif (
            step_name in MECHANICAL_STEPS
            and cache_entry is None
            and artifact_present
            and not (force or fresh)
        ):
            # The file didn't come from this step (e.g. `limits.yaml` was downloaded by
            # `polygon pull`) — it's more up to date than what the step would derive.
            _save_accepted_entry(problem_id, step_name, hashes, problems_dir)
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_CACHE_HIT,
                    f"{primary_path.name} already exists and the step has never run — kept as is "
                    f"('llm generate --step {step_name} --force' derives it again)",
                )
            )
        elif (
            not (force or fresh)
            and is_cache_valid(cache_entry, hashes.input_hash, hashes.prompt_hash, hashes.model)
            and artifact_present
        ):
            result.outcomes.append(
                StepOutcome(
                    step_name,
                    OUTCOME_CACHE_HIT,
                    f"input unchanged (input_hash={hashes.input_hash[:12]}…) — model not called",
                )
            )
        else:
            if fresh:
                rerun_reason = "from scratch (--fresh)"
            elif force:
                rerun_reason = "forced (--force)"
            elif cache_entry is None or cache_entry.status == UNCERTAIN_STATUS:
                rerun_reason = ""
            elif not artifact_present:
                rerun_reason = "regenerated: artifact is missing on disk"
            else:
                rerun_reason = f"regenerated: {_stale_reason(cache_entry, hashes)}"
            revision = (
                None
                if fresh or step_name in MECHANICAL_STEPS
                else _revision_for_step(module, problem_id, spec, cache_entry, hashes, problems_dir)
            )
            if revision is not None:
                mode = f"revising {len(revision.current_paths)} current file(s)"
                rerun_reason = f"{rerun_reason}; {mode}" if rerun_reason else mode
            if rerun_reason:
                logger.info("[%s] %s", step_name, rerun_reason)
            if not _run_step_into(
                result,
                module,
                step_name,
                problem_id,
                spec,
                upstream_artifacts,
                revision=revision,
                saved_response=None,
                detail=rerun_reason,
                prompts_dir=prompts_dir,
                problems_dir=problems_dir,
                templates_dir=templates_dir,
            ):
                return result

        _remember_upstream(step_name, primary_path, upstream_artifacts)

    return result


# StepStatus.state states for `orchestrator <problem> llm status` (CLAUDE.md, "Running":
# "prints a 'step -> cache hit / stale / not run' table").
STATUS_CACHE_HIT = "cache hit"
STATUS_STALE = "stale"
STATUS_NOT_RUN = "not run"
STATUS_UNCERTAIN = "uncertain"
STATUS_CONFLICT = "conflict"
STATUS_SKIPPED_OPTIONAL = "skipped (optional)"
STATUS_BLOCKED = "blocked"

# Additional StepStatus.state states for `orchestrator <problem> llm accept`.
STATUS_ACCEPTED = "accepted"
STATUS_NOT_ACCEPTED = "not accepted"


@dataclass
class StepStatus:
    """The state of one step for `orchestrator <problem> llm status`/`llm accept` —
    computed only from `.cache/*.json`, the files and the current hashes, without calling the
    model."""

    step_name: str
    state: str
    detail: str = ""
    # A short form of `detail` for the console if the full one is long (the full one is in the
    # report); empty — the console gets `detail`.
    brief: str = ""


def compute_step_statuses(
    problem_id: str,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> tuple[list[StepStatus], str | None]:
    """The status of each step for `problem_id`: cache hit / stale / not run /
    uncertain / blocked / skipped (optional) — without a single model call.

    Uses the same hashes (`_compute_step_hashes`) and `is_cache_valid` as
    `run_pipeline`, so "cache hit" here and the actual skipping of the step in
    `run_pipeline` always agree (CLAUDE.md, "Running":
    "using the same hashes as run"). For `stale`, `detail` has what exactly
    changed (`_stale_reason`).

    Returns `(statuses, spec_error)`: if the spec doesn't pass validation,
    the statuses are an empty list, and `spec_error` is the error text.
    """
    return _walk_steps(
        problem_id,
        _status_of_step,
        only_step=None,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
    )


def accept_steps(
    problem_id: str,
    *,
    only_step: str | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> tuple[list[StepStatus], str | None]:
    """`orchestrator <problem> llm accept`: accepts the steps' current artifacts as
    up to date — overwrites the cache of outdated (`stale`) steps with the current
    hashes, without calling the model. Needed when something changed that shouldn't
    change the result (refactoring prompts/templates, a change of the
    `templates/` layout), or when the author adjusted the spec to files already fixed
    by hand: after that `llm generate` reruns only the steps
    whose input changes after the acceptance.

    Not accepted: steps without an artifact on disk, steps without a cache entry
    (`not run` — never generated), steps with `status: uncertain`
    (a failure due to missing data must surface until the spec is fixed) and
    steps with unresolved conflicts (the conflicts directory is still in place).
    A step with resolved conflicts (the directory is deleted) is accepted even if
    its input didn't change. An unapplied saved response of an accepted step
    is deleted.
    The cache entry's status (`confirmed`/`proposed`) is kept, the entry
    gets `accepted: true`.

    The steps are traversed in order: the accepted `constraints.yaml` — the one on
    disk — immediately becomes part of the hash of the following steps, likewise `tests_plan.yaml`.
    Returns `(states, spec_error)` like `compute_step_statuses`.
    """
    return _walk_steps(
        problem_id,
        _accept_step,
        only_step=only_step,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
    )


def _walk_steps(
    problem_id: str,
    visit,
    *,
    only_step: str | None,
    prompts_dir: Path,
    problems_dir: Path,
    templates_dir: Path,
) -> tuple[list[StepStatus], str | None]:
    """The shared traversal of steps for `llm status`/`llm accept`: optional steps,
    picking up upstream artifacts (`_load_upstream`), hashes — as in `run_pipeline`;
    `visit(problem_id, step_name, entry, hashes, artifact_present, problems_dir)`
    decides what to say (and do) about the step."""
    try:
        spec = load_problem_spec(problem_id, problems_dir=problems_dir)
    except SpecValidationError as exc:
        return [], str(exc)

    statuses: list[StepStatus] = []
    upstream_artifacts: dict[str, Any] = {}

    for step_name in LLM_STEPS if only_step is None else [only_step]:
        module = _STEP_MODULES[step_name]

        skip_detail = _optional_skip_detail(step_name, spec, problem_id, problems_dir)
        if skip_detail is not None:
            statuses.append(StepStatus(step_name, STATUS_SKIPPED_OPTIONAL, skip_detail))
            continue

        primary_path = module.primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

        try:
            _load_upstream(step_name, problem_id, spec, upstream_artifacts, problems_dir)
            hashes = _compute_step_hashes(
                problem_id,
                step_name,
                spec,
                upstream_artifacts,
                prompts_dir=prompts_dir,
                templates_dir=templates_dir,
            )
        except (ValueError, SpecReferenceError) as exc:
            statuses.append(StepStatus(step_name, STATUS_BLOCKED, str(exc)))
        else:
            cache_entry = load_cache_entry(problem_id, step_name, problems_dir=problems_dir)
            statuses.append(
                visit(
                    problem_id,
                    step_name,
                    cache_entry,
                    hashes,
                    _artifact_present(primary_path),
                    problems_dir,
                )
            )

        _remember_upstream(step_name, primary_path, upstream_artifacts)

    return statuses, None


def _status_of_step(
    problem_id: str,
    step_name: str,
    cache_entry: CacheEntry | None,
    hashes: _StepHashes,
    artifact_present: bool,
    problems_dir: Path,
) -> StepStatus:
    if cache_entry is not None and cache_entry.conflicts is not None:
        status = StepStatus(
            step_name,
            STATUS_CONFLICT,
            _conflicts_detail(problem_id, step_name, cache_entry, problems_dir),
        )
    else:
        status = _cache_status_of_step(step_name, cache_entry, hashes, artifact_present)
    if load_pending_response(problem_id, step_name, problems_dir=problems_dir) is not None:
        hint = (
            "a saved model response is not applied — "
            f"'orchestrator {problem_address(problems_dir, problem_id)} "
            f"llm apply --step {step_name}'"
        )
        status.detail = f"{status.detail}; {hint}" if status.detail else hint
    return status


def _conflicts_resolved(problem_id: str, entry: CacheEntry, problems_dir: Path) -> bool:
    """The author resolved the conflicts of the previous run — deleted their directory."""
    assert entry.conflicts is not None
    return not (Path(problems_dir) / problem_id / entry.conflicts).exists()


def _conflicts_detail(
    problem_id: str, step_name: str, entry: CacheEntry, problems_dir: Path
) -> str:
    accept = (
        f"'orchestrator {problem_address(problems_dir, problem_id)} llm accept --step {step_name}'"
    )
    if _conflicts_resolved(problem_id, entry, problems_dir):
        return f"conflicts of the last run resolved ({entry.conflicts} deleted) — run {accept}"
    return (
        f"unresolved conflicts of the last run in {entry.conflicts} — resolve by hand, "
        f"delete the directory and run {accept}"
    )


def _cache_status_of_step(
    step_name: str,
    cache_entry: CacheEntry | None,
    hashes: _StepHashes,
    artifact_present: bool,
) -> StepStatus:
    if cache_entry is None:
        if step_name in MECHANICAL_STEPS and artifact_present:
            return StepStatus(
                step_name, STATUS_NOT_RUN, "the file already exists — 'llm generate' keeps it as is"
            )
        return StepStatus(step_name, STATUS_NOT_RUN)
    if cache_entry.status == UNCERTAIN_STATUS:
        return StepStatus(
            step_name,
            STATUS_UNCERTAIN,
            "last run ended with status: uncertain — fix the spec and run again",
        )
    if not artifact_present:
        return StepStatus(step_name, STATUS_STALE, "artifact is missing on disk")
    if is_cache_valid(cache_entry, hashes.input_hash, hashes.prompt_hash, hashes.model):
        return StepStatus(step_name, STATUS_CACHE_HIT)
    return StepStatus(step_name, STATUS_STALE, _stale_reason(cache_entry, hashes))


def _accept_step(
    problem_id: str,
    step_name: str,
    cache_entry: CacheEntry | None,
    hashes: _StepHashes,
    artifact_present: bool,
    problems_dir: Path,
) -> StepStatus:
    current = _status_of_step(
        problem_id, step_name, cache_entry, hashes, artifact_present, problems_dir
    )
    if current.state == STATUS_CACHE_HIT:
        return current
    if current.state == STATUS_CONFLICT:
        assert cache_entry is not None
        if not _conflicts_resolved(problem_id, cache_entry, problems_dir):
            return StepStatus(step_name, STATUS_NOT_ACCEPTED, current.detail)
        if not artifact_present:
            return StepStatus(
                step_name,
                STATUS_NOT_ACCEPTED,
                "conflicts resolved, but the artifact is missing on disk",
            )
        accepted_detail = f"conflicts resolved ({cache_entry.conflicts} deleted)"
        accepted_brief = ""
    elif current.state != STATUS_STALE or not artifact_present:
        detail = current.detail or "the step has never been generated"
        return StepStatus(
            step_name,
            STATUS_NOT_ACCEPTED,
            f"{current.state}: {detail} — run "
            f"'orchestrator {problem_address(problems_dir, problem_id)} "
            f"{_generate_command(step_name)}'",
        )
    else:
        assert cache_entry is not None
        accepted_detail = f"was stale: {current.detail}"
        accepted_brief = f"was stale: {_stale_reason(cache_entry, hashes, brief=True)}"

    assert cache_entry is not None
    _save_accepted_entry(problem_id, step_name, hashes, problems_dir, status=cache_entry.status)
    # The accepted files are up to date — the unapplied response of the same step is no longer needed.
    discard_pending_response(problem_id, step_name, problems_dir=problems_dir)
    return StepStatus(step_name, STATUS_ACCEPTED, accepted_detail, accepted_brief)


def _save_accepted_entry(
    problem_id: str,
    step_name: str,
    hashes: _StepHashes,
    problems_dir: Path,
    *,
    status: str = OUTCOME_CONFIRMED,
) -> None:
    """A cache entry accepting the step's current files as up to date
    (`accepted: true`), without calling the model."""
    save_cache_entry(
        problem_id,
        step_name,
        CacheEntry(
            input_hash=hashes.input_hash,
            prompt_hash=hashes.prompt_hash,
            model=hashes.model,
            status=status,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            input_parts=hashes.input_parts,
            prompt_parts=hashes.prompt_parts,
            input_values=hashes.input_values,
            accepted=True,
        ),
        problems_dir=problems_dir,
    )
