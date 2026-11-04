"""A mechanical step (without a model call) — `limits.yaml` from the TL/ML of
`constraints.yaml`, see `orchestrator/limits.py`.

The step's input is only the TL/ML from `constraints.yaml`, so `limits.yaml`
is overwritten only when they changed (a new run of `constraints_pick`
or an edit of `constraints.yaml`): TL/ML fixed on Polygon (and
downloaded by `polygon pull`) or by hand survive `llm generate`. If
`limits.yaml` already exists but the step hasn't run yet (the file was downloaded by `polygon
pull`), the pipeline accepts the file as is (`pipeline.run_pipeline`);
`llm generate --step limits_from_constraints --force` derives it again.
The previous version of the overwritten file — in `.llm_backup/`, as for the
generative steps.

The interface is the same as the generative steps' (`pipeline._STEP_MODULES`);
the step has no model and prompts, so in the cache instead of the model class and the prompt
hash — `MODEL`/`PROMPT_HASH` (`step_order.MECHANICAL_STEPS`).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from orchestrator.cache import (
    CacheEntry,
    compute_input_values,
    input_parts_from_values,
    save_cache_entry,
)
from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.limits import (
    LIMITS_FILENAME,
    LimitsError,
    parse_limits,
    render_limits,
)
from orchestrator.model_contracts import ModelResponse
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    STATUS_CONFIRMED,
    TEMPLATES_DIR,
    Backup,
    StepResult,
    StepRevision,
    _write_artifacts,
    existing_artifacts,
    require_constraints_yaml,
)

STEP_NAME = "limits_from_constraints"

# Instead of the model class and the prompt hash in the cache entry.
MODEL = "none"
PROMPT_HASH = "none"

# The name of the input part (`cache.compute_input_values`): TL/ML, not the whole file.
_LIMITS_INPUT = f"{CONSTRAINTS_ARTIFACT}: limits"


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """`(spec section, upstream artifacts)` for the hash: the step doesn't need the spec,
    from `constraints.yaml` — only TL/ML. `ValueError` (`LimitsError`) —
    the TL/ML in `constraints.yaml` can't be read: the pipeline stops with this
    error, as without `constraints.yaml` at all."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    try:
        time_limit_ms, memory_limit_mb = parse_limits(constraints_yaml)
    except LimitsError as exc:
        raise LimitsError(
            f"Step '{STEP_NAME}' for '{problem_id}': TL/ML in {CONSTRAINTS_ARTIFACT}: {exc}"
        ) from exc
    return None, {
        _LIMITS_INPUT: {"time_limit_ms": time_limit_ms, "memory_limit_mb": memory_limit_mb}
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def extra_context_documents(spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR) -> None:
    return None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return Path(problems_dir) / problem_id / LIMITS_FILENAME


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    return existing_artifacts(
        {LIMITS_FILENAME: primary_artifact_path(problem_id, spec, problems_dir=problems_dir)}
    )


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
    *,
    revision: StepRevision | None = None,
    saved_response: ModelResponse | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Writes `limits.yaml` from the TL/ML of `constraints.yaml` and the cache entry.
    `revision`/`saved_response` aren't used — there's no model."""
    section, upstream = hash_inputs(problem_id, spec, upstream_artifacts)
    limits = upstream[_LIMITS_INPUT]
    path = primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
    backup = Backup(Path(problems_dir) / problem_id)
    created: list[Path] = []
    written = _write_artifacts(
        {LIMITS_FILENAME: render_limits(limits["time_limit_ms"], limits["memory_limit_mb"])},
        lambda filename: path,
        backup,
        created=created,
    )

    input_values = compute_input_values(section, upstream)
    save_cache_entry(
        problem_id,
        STEP_NAME,
        CacheEntry(
            input_hash=compute_input_hash(problem_id, spec, upstream_artifacts),
            prompt_hash=PROMPT_HASH,
            model=MODEL,
            status=STATUS_CONFIRMED,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            input_parts=input_parts_from_values(input_values),
            prompt_parts={},
            input_values=input_values,
        ),
        problems_dir=problems_dir,
    )
    notes = []
    if backup.directory is not None:
        notes.append(
            {
                "field": LIMITS_FILENAME,
                "kind": "proposed",
                "explanation": (
                    "replaced by the TL/ML from constraints.yaml — the previous TL/ML "
                    f"(possibly set on Polygon) are in {backup.directory}"
                ),
            }
        )
    return StepResult(
        step_name=STEP_NAME,
        problem_id=problem_id,
        status=STATUS_CONFIRMED,
        artifact_paths=written,
        created=created,
        notes=notes,
        backup_dir=backup.directory,
    )
