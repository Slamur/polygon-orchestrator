"""Механический шаг (без вызова модели) — `limits.yaml` из TL/ML
`constraints.yaml`, см. `orchestrator/limits.py`.

Вход шага — только TL/ML из `constraints.yaml`, поэтому `limits.yaml`
перезаписывается, лишь когда они изменились (новый прогон `constraints_pick`
или правка `constraints.yaml`): TL/ML, поправленные на Polygon (и
скачанные `polygon pull`) или руками, переживают `llm generate`. Если
`limits.yaml` уже есть, а шаг ещё не запускался (файл скачал `polygon
pull`), пайплайн принимает файл как есть (`pipeline.run_pipeline`);
`llm generate --step limits_from_constraints --force` выводит его заново.
Прежняя версия перезаписанного файла — в `.llm_backup/`, как у
генеративных шагов.

Интерфейс — тот же, что у генеративных шагов (`pipeline._STEP_MODULES`);
шаг без модели и промптов, поэтому в кэше вместо класса модели и хеша
промптов — `MODEL`/`PROMPT_HASH` (`step_order.MECHANICAL_STEPS`).
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import CacheEntry, compute_input_values, input_parts_from_values, save_cache_entry
from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.limits import LIMITS_FILENAME, LimitsError, parse_limits, render_limits
from orchestrator.model_contracts import ModelResponse
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    STATUS_CONFIRMED,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    _Backup,
    _write_artifacts,
    existing_artifacts,
)
from orchestrator.steps.generators_and_script import (
    CONSTRAINTS_ARTIFACT,
    _require_constraints_yaml,
)

STEP_NAME = "limits_from_constraints"

# Вместо класса модели и хеша промптов в записи кэша.
MODEL = "none"
PROMPT_HASH = "none"

# Имя части входа (`cache.compute_input_values`): TL/ML, а не весь файл.
_LIMITS_INPUT = f"{CONSTRAINTS_ARTIFACT}: limits"


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """`(секция спека, апстрим-артефакты)` для хеша: спек шагу не нужен,
    из `constraints.yaml` — только TL/ML. `ValueError` (`LimitsError`) —
    TL/ML в `constraints.yaml` не читаются: пайплайн останавливается с этой
    ошибкой, как без `constraints.yaml` вовсе."""
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    try:
        time_limit_ms, memory_limit_mb = parse_limits(constraints_yaml)
    except LimitsError as exc:
        raise LimitsError(
            f"Step '{STEP_NAME}' for '{problem_id}': TL/ML in {CONSTRAINTS_ARTIFACT}: {exc}"
        ) from exc
    return None, {_LIMITS_INPUT: {"time_limit_ms": time_limit_ms, "memory_limit_mb": memory_limit_mb}}


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
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
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    revision: Optional[StepRevision] = None,
    saved_response: Optional[ModelResponse] = None,
    prompts_dir: Path = PROMPTS_DIR,
    problems_dir: Path = PROBLEMS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Пишет `limits.yaml` по TL/ML из `constraints.yaml` и запись кэша.
    `revision`/`saved_response` не используются — модели нет."""
    section, upstream = hash_inputs(problem_id, spec, upstream_artifacts)
    limits = upstream[_LIMITS_INPUT]
    path = primary_artifact_path(problem_id, spec, problems_dir=problems_dir)
    backup = _Backup(Path(problems_dir) / problem_id)
    written = _write_artifacts(
        {LIMITS_FILENAME: render_limits(limits["time_limit_ms"], limits["memory_limit_mb"])},
        lambda filename: path,
        backup,
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
        notes=notes,
        backup_dir=backup.directory,
    )
