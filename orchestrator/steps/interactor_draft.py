"""Шаг 6 (опциональный) — черновик интерактора интерактивной задачи, CLAUDE.md,
"Роль каждого генеративного шага", пункт 6.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    existing_artifacts,
    run_generative_step,
)

STEP_NAME = "interactor_draft"

INTERACTOR_FILENAME = "interactor.cpp"


def _require_interactive(problem_id: str, spec: ProblemSpec) -> None:
    if not spec.interactive:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': the spec has no 'interactor' "
            "section — the problem is not interactive, the step is not run "
            "(see CLAUDE.md, generative step roles, item 6)"
        )


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """Секции спека для рендера промпта И для входа хеша кэша — единая точка
    сборки. Помимо секции `interactor` — то, что интерактор читает и как
    общается с решением: формат теста (`formal_input_sketch`, `indexing`,
    input примеров — интерактор читает тест так же, как валидатор) и
    протокол (`interaction_sketch`). Легенда и формат вывода не передаются:
    поведение интерактора задаёт автор, а не сюжет."""
    statement = spec.statement_draft
    return {
        "interactor": spec.interactor.model_dump(mode="json", exclude=SPEC_REFERENCES_FIELD),
        "statement": {
            "formal_input_sketch": statement.formal_input_sketch,
            "indexing": statement.indexing,
            "interaction_sketch": statement.interaction_sketch,
            "sample_examples": [{"input": ex.input} for ex in statement.sample_examples],
        },
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `constraints_pick.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". Как и `run_step`, требует
    интерактивную задачу (секция `interactor` в спеке). `upstream_artifacts`
    не используется — шаг зависит только от спека, присутствует ради единой
    сигнатуры у всех шагов.
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """Что входит в хеш входа шага: `(секции спека, апстрим-артефакты)` —
    общий источник для `compute_input_hash` и хешей по частям
    (`cache.compute_input_values`), чтобы они не разошлись."""
    _require_interactive(problem_id, spec)
    return _build_context_sections(spec), None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Путь к основному артефакту шага — один файл с фиксированным именем,
    как у `checker_draft`."""
    return Path(problems_dir) / problem_id / INTERACTOR_FILENAME


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага на диске: имя артефакта (как его вернула бы
    модель) -> путь — для режима доработки (`base.StepRevision`). `interactor.cpp`."""
    return existing_artifacts(
        {INTERACTOR_FILENAME: Path(problems_dir) / problem_id / INTERACTOR_FILENAME}
    )


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы для конкретного вызова — файлы, которые этому шагу дают
    референсы спека (общие `references` и `interactor.references`, см.
    `orchestrator/references.py`). Единая сигнатура со всеми шагами нужна
    `pipeline.py`, чтобы считать `compute_prompt_hash` одинаково.
    """
    return reference_documents(STEP_NAME, spec)


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
    """Прогоняет шаг `interactor_draft` для `problem_id`.

    Вход: `interactor.behavior_notes` (как интерактор выбирает ответы,
    что считается ошибкой решения, что он пишет для чекера) + формат теста и
    протокол из `statement_draft` (`_build_context_sections`). Модель
    реализует ровно это поведение; если его не хватает для однозначного
    интерактора (например, не сказано, адаптивный ли он, или что делать при
    превышении лимита запросов), шаг обязан вернуть `status: uncertain`, и
    вызов завершится `StepUncertainError` без записи файлов.

    Требует интерактивную задачу (секция `interactor` в спеке) — у обычной
    задачи интерактора нет, шаг не запускается (`pipeline._optional_skip_detail`).

    При успехе пишет `problems/<problem_id>/interactor.cpp` и проверяет его
    компиляцию. `upstream_artifacts` не используется.
    """
    _require_interactive(problem_id, spec)

    context = {"problem_id": spec.problem_id, **_build_context_sections(spec)}
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        return Path(problems_dir) / problem_id / filename

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
