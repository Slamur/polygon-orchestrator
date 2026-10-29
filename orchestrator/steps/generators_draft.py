"""Шаг 3, вторая часть — генераторы (C++/testlib) по плану тестов, CLAUDE.md, "Роль
каждого генеративного шага", пункт 3.

Механическая часть подготовки тестов: каждый генератор плана
(`authoring/tests_plan.yaml`, шаг `tests_plan`) — файл
`generators/<name>.cpp` с параметрами из плана. Спек шаг не видит, кроме
формата ввода: что и как генерировать, решено в плане.

Вход по частям — каждый генератор плана отдельно (`upstream: tests_plan.yaml:
<name>`): правка одной записи плана в режиме доработки показывается модели
как правка одного генератора, а правка тестов плана (`tests`, `constants`)
этот шаг не трогает вовсе.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    CONSTRAINTS_ARTIFACT,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    artifact_filename,
    MissingArtifactsError,
    constraints_without_limits,
    directory_artifacts,
    require_constraints_yaml,
    run_generative_step,
    statement_io_section,
)
from orchestrator.steps.tests_plan import (
    TESTS_PLAN_ARTIFACT,
    dump_plan_part,
    load_tests_plan,
    plan_generators,
)

STEP_NAME = "generators_draft"

# Каталог генераторов внутри каталога задачи.
GENERATORS_SUBDIR = "generators"

# `opt<T>("name")` в генераторе — параметр командной строки testlib.
_OPT_RE = re.compile(r"\bopt\s*<[^>]*>\s*\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\"")


def _plan_key(name: str) -> str:
    return f"{TESTS_PLAN_ARTIFACT}: {name}"


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """`(секция спека, апстрим-артефакты)`: формат ввода из `statement_draft`;
    `constraints.yaml` без TL/ML (гарантии входа, которые проверяет
    валидатор, — генератор обязан их соблюдать) и каждый генератор плана
    отдельной частью."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    upstream = {CONSTRAINTS_ARTIFACT: constraints_without_limits(constraints_yaml)}
    for generator in plan_generators(plan):
        upstream[_plan_key(generator["name"])] = dump_plan_part(generator)
    return {"statement": statement_io_section(spec, include_output_and_legend=False)}, upstream


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы — генераторы из референсов спека (`orchestrator/references.py`)."""
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Каталог генераторов: их имена задаёт план, поэтому `pipeline.py`
    считает файлы шага на месте, если каталог есть и не пуст."""
    return Path(problems_dir) / problem_id / GENERATORS_SUBDIR


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага для режима доработки: `generators/*.cpp` под
    именами файлов."""
    return directory_artifacts(primary_artifact_path(problem_id, spec, problems_dir=problems_dir), "*.cpp")


def generator_params(source: str) -> list[str]:
    """Имена параметров `opt<T>("name")` в коде генератора, без повторов."""
    return list(dict.fromkeys(_OPT_RE.findall(source)))


def plan_mismatch_notes(
    plan: dict[str, Any], generators_dir: Path
) -> list[dict[str, Any]]:
    """Заметки `plan_mismatch` после записи файлов: генератор на диске,
    которого нет в плане, и генератор, чьи `opt<T>(...)` не совпадают с
    параметрами плана, — test-script строится по плану и вызовет его с
    другими параметрами."""
    expected = {g["name"]: [p["name"] for p in g.get("params") or []] for g in plan_generators(plan)}
    notes: list[dict[str, Any]] = []
    for path in sorted(Path(generators_dir).glob("*.cpp")):
        name = path.stem
        if name not in expected:
            notes.append(_note(
                f"generators/{path.name}",
                f"not in {TESTS_PLAN_ARTIFACT}: test_script_draft will not use it — "
                "add it to the plan or delete the file",
            ))
            continue
        actual = generator_params(path.read_text(encoding="utf-8"))
        if set(actual) != set(expected[name]):
            notes.append(_note(
                f"generators/{path.name}",
                f"opt parameters {actual} differ from the plan {expected[name]}",
            ))
    return notes


def _note(field: str, explanation: str) -> dict[str, Any]:
    return {"field": field, "kind": "plan_mismatch", "explanation": explanation}


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
    """Прогоняет шаг `generators_draft` для `problem_id`.

    Вход: генераторы плана (`upstream_artifacts["tests_plan.yaml"]`), формат
    ввода из `statement_draft`, `constraints.yaml` без TL/ML. Выход — по
    файлу `generators/<name>.cpp` на генератор плана: недостающие модель
    один раз дописывает отдельным вызовом (`MissingArtifactsError`), о
    генераторах, которых в плане нет, — заметка. Файлы независимы и
    применяются по одному (`partial_apply`); после записи — сверка
    параметров с планом (`plan_mismatch_notes`).
    """
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    generators = plan_generators(plan)
    context = {
        "problem_id": spec.problem_id,
        "statement": statement_io_section(spec, include_output_and_legend=False),
        "constraints_yaml": constraints_without_limits(constraints_yaml),
        "generators": [
            {"name": generator["name"], "plan": dump_plan_part(generator)} for generator in generators
        ],
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))
    expected = {f"{generator['name']}.cpp" for generator in generators}
    generators_dir = primary_artifact_path(problem_id, spec, problems_dir=problems_dir)

    def resolve_artifact_path(filename: str) -> Path:
        if not filename.endswith(".cpp"):
            raise ValueError(f"Step '{STEP_NAME}' returns only generators/*.cpp, got '{filename}'")
        return generators_dir / artifact_filename(filename, GENERATORS_SUBDIR)

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        # Лишние генераторы — не ошибка ответа (например, прежний генератор,
        # о котором модель промолчала в режиме доработки): о них заметка
        # `plan_mismatch_notes`.
        missing = sorted(expected - {resolve_artifact_path(name).name for name in artifacts})
        if missing:
            raise MissingArtifactsError(
                f"Step '{STEP_NAME}': incomplete response — missing generators: {missing}", missing
            )

    result = run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        # Генераторы независимы: применяются по одному.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
    if result.not_applied_reason is None:
        result.notes.extend(plan_mismatch_notes(plan, generators_dir))
    return result
