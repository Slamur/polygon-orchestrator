"""Шаг 1 — черновик условия задачи в LaTeX (CLAUDE.md, "Роль каждого
генеративного шага", пункт 1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    OUTPUTS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    run_generative_step,
    with_default_lists,
)

STEP_NAME = "statement_draft"

# Единственное Optional[list[...]]-поле секции, по которому user.md.j2
# делает {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["known_ambiguities"]

STATEMENT_SUBDIR = "statement"
_FIXED_STATEMENT_FILES = ["legend.tex", "input_format.tex", "output_format.tex", "notes.tex"]


def _expected_artifact_names(spec: ProblemSpec) -> set[str]:
    """Ровно тот набор имён файлов, который `prompts/statement_draft/`
    требует от модели: 4 фиксированных tex-файла + по одному
    `examples/example_<N>.txt` на каждый `sample_examples[N-1]`.
    """
    n = len(spec.statement_draft.sample_examples)
    return set(_FIXED_STATEMENT_FILES) | {
        f"examples/example_{i}.txt" for i in range(1, n + 1)
    }


def _validate_statement_artifacts(expected: set[str]) -> Callable[[dict[str, str]], None]:
    def _validate(artifacts: dict[str, str]) -> None:
        actual = set(artifacts.keys())
        missing = expected - actual
        extra = actual - expected
        if missing or extra:
            parts = []
            if missing:
                parts.append(f"не хватает: {sorted(missing)}")
            if extra:
                parts.append(f"лишние: {sorted(extra)}")
            raise ValueError(
                f"Шаг '{STEP_NAME}': набор файлов от модели не совпадает "
                f"с ожидаемым ({'; '.join(parts)})"
            )

    return _validate


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели — использует `pipeline.py`, чтобы
    решить, валиден ли кэш, до того как решать, вызывать ли `run_step`
    (CLAUDE.md, "Кэширование по хешу спека"). `problem_id`/`upstream_artifacts`
    здесь не используются (шаг от них не зависит) — присутствуют только ради
    единой сигнатуры `compute_input_hash(problem_id, spec, upstream_artifacts)`
    у всех пяти шагов.
    """
    section_data = with_default_lists(
        spec.statement_draft.model_dump(mode="json"), _LIST_FIELDS
    )
    return _cache_compute_input_hash(STEP_NAME, section_data)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, outputs_dir: Path = OUTPUTS_DIR
) -> Path:
    """Как у `solutions_draft` — каталог, а не файл: `pipeline.py` считает
    кэш валидным, если каталог существует и не пуст. Раньше здесь был путь к
    единственному `statement.tex` — теперь у шага несколько файлов в
    подпапке `statement/`.
    """
    return Path(outputs_dir) / problem_id / STATEMENT_SUBDIR


def extra_context_documents(spec: ProblemSpec) -> Optional[list[str]]:
    """`statement_draft` не имеет документов контекста, специфичных для
    конкретного вызова — только фиксированный `STEP_CONTEXT_DOCUMENTS`
    (см. `orchestrator.steps.base`). Присутствует ради единой сигнатуры,
    которую использует `pipeline.py` (см.
    `generators_and_script.extra_context_documents`).
    """
    return None


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `statement_draft` для `problem_id`.

    Вход: секция `statement_draft` спека (легенда, формальные черновики
    ввода/вывода, индексация, `sample_examples`, `known_ambiguities`,
    `preserve_legend_verbatim`) — больше ничего из спека этому шагу не
    передаётся. `context` строится из `model_dump()` всей секции, так что
    новые поля секции (как `preserve_legend_verbatim`) автоматически
    попадают и в промпт, и в `input_hash` — руками их сюда прокидывать не
    нужно.

    Шагу запрещено додумывать асимптотику решения по легенде задачи — это
    исключительная зона шага `constraints_pick` (CLAUDE.md, "Роль каждого
    генеративного шага", п.1: "модель **не имеет права** сама придумывать
    асимптотику по легенде"). Если в спеке не хватает данных для формального
    описания ввода/вывода, индексации или точности вещественного ответа,
    промпт обязан вернуть `status: uncertain`, и этот вызов завершится
    `StepUncertainError`, не записав ни одного файла (см. "Правило эскалации
    при неуверенности").

    При успехе пишет в `outputs/<problem_id>/statement/` 4 фиксированных
    файла (`legend.tex`, `input_format.tex`, `output_format.tex`,
    `notes.tex`) и по одному `examples/example_<N>.txt` на каждый элемент
    `sample_examples` (только input примера). Набор имён файлов из ответа
    модели проверяется ДО записи на диск (`_validate_statement_artifacts`):
    при любой недостаче/лишнем файле бросается `ValueError`, и ни один файл
    не пишется.

    `upstream_artifacts` не используется: `statement_draft` не зависит от
    результатов других шагов (см. CLAUDE.md, "Кэширование по хешу спека" —
    для этого шага апстрим-артефактов в хеше нет). Параметр присутствует
    только ради общей сигнатуры `run_step(problem_id, spec,
    upstream_artifacts)` у всех пяти шагов.
    """
    section_data = with_default_lists(
        spec.statement_draft.model_dump(mode="json"), _LIST_FIELDS
    )
    context = {"problem_id": problem_id, "statement_draft": section_data}
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)

    def resolve_artifact_path(filename: str) -> Path:
        # filename приходит как "legend.tex" или "examples/example_1.txt" —
        # вложенный каталог создаст _write_artifacts (parent.mkdir).
        return Path(outputs_dir) / problem_id / STATEMENT_SUBDIR / filename

    expected = _expected_artifact_names(spec)

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        outputs_dir=outputs_dir,
        templates_dir=templates_dir,
        validate_artifacts=_validate_statement_artifacts(expected),
    )
