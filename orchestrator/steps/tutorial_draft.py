"""Шаг 7 (по команде) — разбор задачи по драфту автора, CLAUDE.md, "Роль
каждого генеративного шага", пункт 7.

Запускается только `orchestrator <задача> llm tutorial` (`step_order.ON_DEMAND_STEPS`).
Вход читается из файлов задачи, а не из спека: драфт `authoring/draft-tutorial.md`,
условие на языке генерации `statement/<код>/*.tex` и решения, которые автор указал в `--ref`. Спек даёт
только примеры: остальное (`algorithm_hint`, `known_wrong_approaches`) модель не
получает, чтобы ей не из чего было дописывать своё.

Список `--ref` — параметр команды, а `llm status|apply|accept` и следующий запуск
без `--ref` должны считать тот же хеш входа, поэтому список запоминается: он
восстанавливается из частей входа прошлого прогона (`remembered_refs`).

Файлы входа передаются в `upstream_artifacts` одним ключом `INPUTS_KEY`, его
заполняет `pipeline.py` (`read_inputs`): единый интерфейс шагов не знает
каталога задачи при подсчёте хеша.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    TUTORIAL_DRAFT_FILENAME,
    TUTORIAL_FILENAME,
    authoring_path,
    is_removed_mark,
)
from orchestrator.spec import ProblemSpec
from orchestrator.statement_languages import DEFAULT_LANGUAGE, language_name
from orchestrator.steps.base import (
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    StepRevision,
    check_artifact_names,
    existing_artifacts,
    language_input,
    run_generative_step,
)
from orchestrator.steps.solutions_draft import SOLUTIONS_SUBDIR
from orchestrator.steps.statement_draft import STATEMENT_SUBDIR, language_dir

STEP_NAME = "tutorial_draft"

# Ключ файлов входа в `upstream_artifacts`: путь относительно каталога задачи
# -> содержимое (`read_inputs`).
INPUTS_KEY = "tutorial_inputs"

_DRAFT_KEY = f"{AUTHORING_DIRNAME}/{TUTORIAL_DRAFT_FILENAME}"
# Как части `upstream_artifacts` называются в `CacheEntry.input_values`
# (`cache.compute_input_values`).
_INPUT_VALUE_SOLUTION_PREFIX = f"upstream: {SOLUTIONS_SUBDIR}/"


def draft_path(problem_id: str, *, problems_dir: Path = PROBLEMS_DIR) -> Path:
    return authoring_path(Path(problems_dir) / problem_id, TUTORIAL_DRAFT_FILENAME)


def _solution_name(ref: str) -> str:
    """Имя файла решения по значению `--ref`: `ok.cpp` или `solutions/ok.cpp`."""
    path = PurePosixPath(ref)
    parts = path.parts[1:] if path.parts[:1] == (SOLUTIONS_SUBDIR,) else path.parts
    if len(parts) != 1 or parts[0] == "..":
        raise ValueError(
            f"Step '{STEP_NAME}': --ref '{ref}' is not a file of {SOLUTIONS_SUBDIR}/ "
            "(expected a file name, e.g. --ref ok.cpp)"
        )
    return parts[0]


def read_inputs(
    problem_id: str,
    refs: list[str],
    *,
    language: str = DEFAULT_LANGUAGE,
    problems_dir: Path = PROBLEMS_DIR,
) -> dict[str, str]:
    """Файлы входа шага (см. докстринг модуля): путь относительно каталога
    задачи -> содержимое; файлы условия — под путём без каталога языка
    (`statement/legend.tex`), как до языков, чтобы перенос раскладки не менял
    хеш входа. Нет или пуст драфт, нет условия на языке `language`, нет
    решения из `refs` — `ValueError`: без них вызывать модель незачем."""
    problem_dir = Path(problems_dir) / problem_id
    path = draft_path(problem_id, problems_dir=problems_dir)
    if not path.is_file():
        raise ValueError(f"Step '{STEP_NAME}': the tutorial draft {path} does not exist")
    draft = path.read_text(encoding="utf-8")
    if not draft.strip():
        raise ValueError(f"Step '{STEP_NAME}': the tutorial draft {path} is empty")

    statement_dir = problem_dir / STATEMENT_SUBDIR / language
    statement_files = [
        statement_file
        for statement_file in sorted(statement_dir.glob("*.tex"))
        if statement_file.is_file() and statement_file.name != TUTORIAL_FILENAME
    ]
    if not statement_files:
        raise ValueError(
            f"Step '{STEP_NAME}': no statement in {statement_dir} — "
            "the tutorial is written in the notation of the statement"
        )
    inputs = {_DRAFT_KEY: draft}
    for statement_file in statement_files:
        inputs[f"{STATEMENT_SUBDIR}/{statement_file.name}"] = statement_file.read_text(
            encoding="utf-8"
        )

    for name in sorted({_solution_name(ref) for ref in refs}):
        solution = problem_dir / SOLUTIONS_SUBDIR / name
        if is_removed_mark(name) or not solution.is_file():
            raise ValueError(
                f"Step '{STEP_NAME}': the solution '{name}' from --ref is not in "
                f"{solution.parent} — pass the new list with --ref, or --no-ref for none"
            )
        inputs[f"{SOLUTIONS_SUBDIR}/{name}"] = solution.read_text(encoding="utf-8")
    return inputs


def remembered_refs(input_values: dict[str, Any] | None) -> list[str]:
    """Решения прошлого прогона — по частям его входа (`CacheEntry.input_values`)."""
    return sorted(
        name[len(_INPUT_VALUE_SOLUTION_PREFIX) :]
        for name in input_values or {}
        if name.startswith(_INPUT_VALUE_SOLUTION_PREFIX)
    )


def solution_names(inputs: dict[str, str]) -> list[str]:
    """Имена решений среди файлов входа (`read_inputs`)."""
    prefix = f"{SOLUTIONS_SUBDIR}/"
    return [name[len(prefix) :] for name in inputs if name.startswith(prefix)]


def _require_inputs(problem_id: str, upstream_artifacts: dict[str, Any] | None) -> dict[str, str]:
    if not upstream_artifacts or INPUTS_KEY not in upstream_artifacts:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': upstream_artifacts has no "
            f"'{INPUTS_KEY}' — the pipeline reads them from the problem files"
        )
    return upstream_artifacts[INPUTS_KEY]


def _section_data(spec: ProblemSpec) -> dict[str, Any]:
    return {
        "sample_examples": [
            {"input": example.input, "output": example.output}
            for example in spec.statement_draft.sample_examples
        ],
        **language_input(spec),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `constraints_pick.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека"."""
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """Что входит в хеш входа шага: `(секция спека, апстрим-артефакты)` —
    общий источник для `compute_input_hash` и хешей по частям
    (`cache.compute_input_values`), чтобы они не разошлись. Каждый файл входа
    — отдельная часть: режим доработки показывает модели его "было → стало"."""
    return _section_data(spec), _require_inputs(problem_id, upstream_artifacts)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    return language_dir(problem_id, spec, problems_dir=problems_dir) / TUTORIAL_FILENAME


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага на диске: имя артефакта (как его вернула бы
    модель) -> путь — для режима доработки (`base.StepRevision`). Разбор,
    написанный автором или скачанный `polygon pull`, — такой же текущий файл:
    модель его дорабатывает, а не пишет заново."""
    return existing_artifacts(
        {TUTORIAL_FILENAME: primary_artifact_path(problem_id, spec, problems_dir=problems_dir)}
    )


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> list[str] | None:
    """Референсов спека у шага нет: решения из `--ref` — часть входа, а не
    образцы. Единая сигнатура со всеми шагами нужна `pipeline.py`."""
    return None


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
    """Прогоняет шаг `tutorial_draft` для `problem_id`: пишет
    `problems/<problem_id>/statement/tutorial.tex` (см. докстринг модуля)."""
    inputs = _require_inputs(problem_id, upstream_artifacts)

    def files_under(subdir: str) -> list[dict[str, str]]:
        prefix = f"{subdir}/"
        return [
            {"name": name, "content": content.rstrip("\n")}
            for name, content in sorted(inputs.items())
            if name.startswith(prefix)
        ]

    context = {
        "problem_id": spec.problem_id,
        "draft": inputs[_DRAFT_KEY].rstrip("\n"),
        "statement_files": files_under(STATEMENT_SUBDIR),
        "solutions": files_under(SOLUTIONS_SUBDIR),
        **_section_data(spec),
        # После `_section_data`: в промпт — название языка, а не имя Polygon.
        "language": language_name(spec.language),
    }

    def resolve_artifact_path(filename: str) -> Path:
        return language_dir(problem_id, spec, problems_dir=problems_dir) / filename

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        check_artifact_names(STEP_NAME, {TUTORIAL_FILENAME}, artifacts)

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=compute_input_hash(problem_id, spec, upstream_artifacts),
        input_values=compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts)),
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        validate_artifacts=validate_artifacts,
    )
