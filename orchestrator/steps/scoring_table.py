"""A mechanical step (without a model call) — the statement's subtasks table
`statement/<code>/scoring.tex`, see `orchestrator/scoring_table.py`.

It runs after `run_tests`: the points of one test of an `each_test` group (`30 (2)`)
need the number of the group's tests, which `run_tests` records in its report
(`group_counts`). The input: the statement language, the groups of `constraints.yaml`
(name, points, dependencies, feedback and points policies), the constraints cells the
`statement_draft` model wrote (`authoring/scoring.yaml`) and the number of tests of each
group. The table is rewritten only when the input changed; a different table on disk (an
edit by hand or `polygon pull`) goes to `.llm_backup/` with a note, as for
`limits_from_constraints`. A problem without test groups — the step is skipped.

The interface is the same as the generative steps' (`pipeline._STEP_MODULES`); the step
has no model and prompts (`step_order.MECHANICAL_STEPS`).
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
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import authoring_path
from orchestrator.scoring_table import (
    SCORING_TEXTS_FILENAME,
    ScoringGroup,
    ScoringTableError,
    parse_scoring_texts,
    render_scoring,
)
from orchestrator.spec import ProblemSpec
from orchestrator.statement_languages import STATEMENT_DIRNAME
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
from orchestrator.steps.solutions_check import load_run_report
from orchestrator.steps.tests_check import GROUP_COUNTS_KEY
from orchestrator.steps.tests_check import STEP_NAME as TESTS_STEP
from orchestrator.test_groups import TestGroupsError, load_test_groups, parse_test_groups

STEP_NAME = "scoring_table"
SCORING_FILE = "scoring.tex"

# Instead of the model class and the prompt hash in the cache entry.
MODEL = "none"
PROMPT_HASH = "none"

INPUTS_KEY = f"{STEP_NAME}: inputs"
_GROUPS_INPUT = f"{CONSTRAINTS_ARTIFACT}: test_groups"
_TEXTS_INPUT = f"authoring/{SCORING_TEXTS_FILENAME}"
_COUNTS_INPUT = f"{TESTS_STEP}: tests per group"


def skip_detail(
    spec: ProblemSpec, *, problem_id: str, problems_dir: Path = PROBLEMS_DIR, **_: Any
) -> str | None:
    """No groups in the spec or in `constraints.yaml` (the table is assembled from the
    latter) — nothing to assemble. Invalid groups aren't a skip: the step reports them."""
    if not spec.constraints.test_groups_hint:
        return "spec has no test groups — no subtasks table"
    try:
        groups = load_test_groups(Path(problems_dir) / problem_id)
    except TestGroupsError:
        return None
    if not groups:
        return "constraints.yaml has no test groups — no subtasks table"
    return None


def load_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Any]:
    """`scoring.yaml` as text (`None` — no file) and the number of tests of each group from
    the last passed `run_tests` (`None` — none recorded)."""
    path = authoring_path(Path(problems_dir) / problem_id, SCORING_TEXTS_FILENAME)
    texts = path.read_text(encoding="utf-8") if path.is_file() else None
    report = load_run_report(problem_id, TESTS_STEP, problems_dir=problems_dir) or {}
    counts = report.get(GROUP_COUNTS_KEY)
    return {"texts": texts, "counts": counts if isinstance(counts, dict) else None}


def _groups(problem_id: str, upstream_artifacts: dict[str, Any] | None) -> list[ScoringGroup]:
    text = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    try:
        hints = parse_test_groups(text) or []
    except TestGroupsError as exc:
        raise ScoringTableError(f"Step '{STEP_NAME}' for '{problem_id}': {exc}") from exc
    return [
        ScoringGroup(
            name=hint.name,
            points=hint.points,
            dependencies=tuple(hint.dependencies or ()),
            feedback_policy=hint.feedback_policy,
            points_policy=hint.points_policy,
        )
        for hint in hints
    ]


def _inputs(problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict[str, Any]:
    if not upstream_artifacts or INPUTS_KEY not in upstream_artifacts:
        raise ValueError(f"Step '{STEP_NAME}' for '{problem_id}': its input files aren't loaded")
    return upstream_artifacts[INPUTS_KEY]


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    inputs = _inputs(problem_id, upstream_artifacts)
    groups = [
        {
            "name": group.name,
            "points": group.points,
            "dependencies": list(group.dependencies),
            "feedback_policy": group.feedback_policy,
            "points_policy": group.points_policy,
        }
        for group in _groups(problem_id, upstream_artifacts)
    ]
    return {"language": spec.language}, {
        _GROUPS_INPUT: groups,
        _TEXTS_INPUT: inputs["texts"],
        _COUNTS_INPUT: inputs["counts"],
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
    return Path(problems_dir) / problem_id / STATEMENT_DIRNAME / spec.language / SCORING_FILE


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    return existing_artifacts(
        {SCORING_FILE: primary_artifact_path(problem_id, spec, problems_dir=problems_dir)}
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
    """Writes `scoring.tex` and the cache entry. `ScoringTableError` — no `scoring.yaml`,
    it doesn't match the groups, or the tests per group aren't known.
    `revision`/`saved_response` aren't used — there's no model."""
    inputs = _inputs(problem_id, upstream_artifacts)
    groups = _groups(problem_id, upstream_artifacts)
    where = f"Step '{STEP_NAME}' for '{problem_id}'"
    if inputs["texts"] is None:
        raise ScoringTableError(
            f"{where}: no authoring/{SCORING_TEXTS_FILENAME} — the constraints of the "
            "subtasks are written by statement_draft"
        )
    try:
        texts = parse_scoring_texts(inputs["texts"], [group.name for group in groups])
        table = render_scoring(spec.language, groups, texts, inputs["counts"] or {})
    except ScoringTableError as exc:
        raise ScoringTableError(f"{where}: {exc}") from exc

    path = primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
    backup = Backup(Path(problems_dir) / problem_id)
    created: list[Path] = []
    written = _write_artifacts({SCORING_FILE: table}, lambda _: path, backup, created=created)

    section, upstream = hash_inputs(problem_id, spec, upstream_artifacts)
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
                "field": SCORING_FILE,
                "kind": "proposed",
                "explanation": (
                    "replaced by the table assembled from constraints.yaml, "
                    f"authoring/{SCORING_TEXTS_FILENAME} and the tests — the previous table "
                    f"(possibly edited by hand or on Polygon) is in {backup.directory}"
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
