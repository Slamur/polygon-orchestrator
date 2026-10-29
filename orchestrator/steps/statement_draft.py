"""Шаг 1 — черновик условия задачи в LaTeX (CLAUDE.md, "Роль каждого
генеративного шага", пункт 1).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.cache import compute_input_values
from orchestrator.model_contracts import ModelResponse
from orchestrator.references import reference_documents
from orchestrator.spec import SPEC_REFERENCES_FIELD, ProblemSpec
from orchestrator.steps.base import (
    INTERACTIVE_TUTORIAL,
    PROBLEMS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    GeneratedArtifacts,
    StepResult,
    StepRevision,
    check_artifact_names,
    existing_artifacts,
    run_generative_step,
    with_default_lists,
)

STEP_NAME = "statement_draft"

# Единственное Optional[list[...]]-поле секции, по которому user.md.j2
# делает {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["known_ambiguities"]

STATEMENT_SUBDIR = "statement"
_FIXED_STATEMENT_FILES = ["legend.tex", "input_format.tex", "output_format.tex", "notes.tex"]
_EXAMPLE_NAME = re.compile(r"^example_(\d+)\.txt$")

# Интерактивная задача (`spec.interactive`): протокол взаимодействия —
# отдельный блок условия (Interaction в Polygon), а пример в условии —
# диалог решения с интерактором, а не вход теста (его читает интерактор):
# что решение прочитало (`input_for_statement`) и что вывело
# (`output_for_statement`), см. `upload_statement`. У обычной задачи те же
# файлы — input/output for statements, которые автор задаёт сам (руками или
# на Polygon, их скачивает `polygon pull`): это не артефакты шага, модель их
# не видит и не пишет.
INTERACTION_FILE = "interaction.tex"
_DIALOGUE_NAME = re.compile(r"^example_(\d+)\.(input|output)_for_statement\.txt$")


def dialogue_filenames(number: int) -> tuple[str, str]:
    """Имена файлов диалога `number`-го примера (без `examples/`)."""
    return (
        f"example_{number}.input_for_statement.txt",
        f"example_{number}.output_for_statement.txt",
    )


def verify_filename(number: int) -> str:
    """Имя файла флага "verify input/output for statements" `number`-го
    примера (без `examples/`): файл есть — флаг включён. Пишет автор или
    `polygon pull`, не модель."""
    return f"example_{number}.verify_for_statement.txt"


def _expected_artifact_names(spec: ProblemSpec) -> set[str]:
    """Ровно тот набор имён файлов шага: 4 фиксированных tex-файла + по
    одному `examples/example_<N>.txt` на каждый `sample_examples[N-1]`; у
    интерактивной задачи — ещё `interaction.tex` и диалог каждого примера.
    `example_<N>.txt` пишет оркестратор (`_example_inputs`), остальное —
    модель (`prompts/statement_draft/`).
    """
    n = len(spec.statement_draft.sample_examples)
    names = set(_FIXED_STATEMENT_FILES) | {
        f"examples/example_{i}.txt" for i in range(1, n + 1)
    }
    if spec.interactive:
        names.add(INTERACTION_FILE)
        for i in range(1, n + 1):
            names.update(f"examples/{name}" for name in dialogue_filenames(i))
    return names


def _section_data(spec: ProblemSpec) -> dict[str, Any]:
    """Секция `statement_draft` для промпта и хеша. `interaction_sketch`
    только у интерактивной задачи — у обычной поля в хеше нет, как и до его
    появления (иначе его добавление инвалидировало бы кэш всех задач)."""
    exclude = set(SPEC_REFERENCES_FIELD)
    if not spec.interactive:
        exclude.add("interaction_sketch")
    return with_default_lists(
        spec.statement_draft.model_dump(mode="json", exclude=exclude), _LIST_FIELDS
    )


def _example_inputs(spec: ProblemSpec) -> dict[str, str]:
    """`examples/example_<N>.txt` — input `sample_examples[N-1]` как есть.
    Это копия из спека, а не текст условия, поэтому её пишет оркестратор, а
    не модель: модель могла пропустить файл или изменить input."""
    return {
        f"examples/example_{i}.txt": example.input
        for i, example in enumerate(spec.statement_draft.sample_examples, start=1)
    }


def _stale_example_inputs(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path
) -> dict[str, Path]:
    """`example_<N>.txt` на диске, которых больше нет в `sample_examples`
    (примеров стало меньше), — их пометят `.removed`."""
    examples_dir = Path(problems_dir) / problem_id / STATEMENT_SUBDIR / "examples"
    if not examples_dir.is_dir():
        return {}
    n = len(spec.statement_draft.sample_examples)
    return {
        f"examples/{path.name}": path
        for path in sorted(examples_dir.glob("example_*.txt"))
        if (match := _EXAMPLE_NAME.match(path.name)) and int(match.group(1)) > n
    }


def _validate_statement_artifacts(expected: set[str]) -> Callable[[dict[str, str]], None]:
    def _validate(artifacts: dict[str, str]) -> None:
        check_artifact_names(STEP_NAME, expected, artifacts)

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
    у всех шагов.
    """
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """Что входит в хеш входа шага: `(секция спека, апстрим-артефакты)` —
    общий источник для `compute_input_hash` и хешей по частям
    (`cache.compute_input_values`), чтобы они не разошлись."""
    return _section_data(spec), None


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Как у `solutions_draft` — каталог, а не файл: `pipeline.py` считает
    кэш валидным, если каталог существует и не пуст. Раньше здесь был путь к
    единственному `statement.tex` — теперь у шага несколько файлов в
    подпапке `statement/`.
    """
    return Path(problems_dir) / problem_id / STATEMENT_SUBDIR


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага на диске: имя артефакта (как его вернула бы
    модель) -> путь — для режима доработки (`base.StepRevision`).
    4 tex-файла, `interaction.tex` и диалоги примеров
    `examples/example_<N>.{input,output}_for_statement.txt` (по возрастанию
    N) — только у интерактивной задачи: у обычной это input/output for
    statements автора. Не артефакты модели: `examples/example_<N>.txt` (их
    пишет оркестратор из спека, `_example_inputs`), флаги
    `example_<N>.verify_for_statement.txt` и `name.txt` (его пишут
    `polygon push`/`pull`)."""
    statement_dir = Path(problems_dir) / problem_id / STATEMENT_SUBDIR
    candidates = {
        name: statement_dir / name for name in [*_FIXED_STATEMENT_FILES, INTERACTION_FILE]
    }
    examples_dir = statement_dir / "examples"
    examples = (
        examples_dir.glob("example_*.txt") if spec.interactive and examples_dir.is_dir() else []
    )
    numbered = sorted(
        (int(match.group(1)), path.name, path)
        for path in examples
        if (match := _DIALOGUE_NAME.match(path.name))
    )
    candidates.update({f"examples/{name}": path for _, name, path in numbered})
    return existing_artifacts(candidates)


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы для конкретного вызова — файлы, которые этому шагу дают
    референсы спека (общие `references` и `statement_draft.references`, см.
    `orchestrator/references.py`). Единая сигнатура со всеми шагами нужна
    `pipeline.py`, чтобы считать `compute_prompt_hash` одинаково.

    Интерактивной задаче — ещё правила `INTERACTIVE_TUTORIAL` (перед
    референсами): обычным задачам они не нужны, и их правка не трогает кэш
    условия обычных задач.
    """
    references = reference_documents(STEP_NAME, spec) or []
    rules = [str(Path(templates_dir) / INTERACTIVE_TUTORIAL)] if spec.interactive else []
    return [*rules, *references] or None


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

    При успехе пишет в `problems/<problem_id>/statement/` 4 фиксированных
    файла (`legend.tex`, `input_format.tex`, `output_format.tex`,
    `notes.tex`) и по одному `examples/example_<N>.txt` на каждый элемент
    `sample_examples` (только input примера — копия из спека, её пишет
    оркестратор, а не модель); у интерактивной задачи
    (`spec.interactive`) — ещё `interaction.tex` (протокол взаимодействия)
    и диалог каждого примера (`example_<N>.input_for_statement.txt`,
    `example_<N>.output_for_statement.txt`). Набор имён файлов из ответа
    модели проверяется ДО записи на диск (`_validate_statement_artifacts`):
    недостающие файлы дозапрашиваются у модели один раз, а если их всё равно
    нет или есть лишние — ответ не применяется целиком (конфликт), ни один
    файл не пишется. `example_<N>.txt`, которых больше нет в спеке,
    помечаются `.removed`.

    `upstream_artifacts` не используется: `statement_draft` не зависит от
    результатов других шагов (см. CLAUDE.md, "Кэширование по хешу спека" —
    для этого шага апстрим-артефактов в хеше нет). Параметр присутствует
    только ради общей сигнатуры `run_step(problem_id, spec,
    upstream_artifacts)` у всех шагов.
    """
    context = {"problem_id": spec.problem_id, "statement_draft": _section_data(spec)}
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        # filename приходит как "legend.tex" или "examples/example_1.txt" —
        # вложенный каталог создаст _write_artifacts (parent.mkdir).
        return Path(problems_dir) / problem_id / STATEMENT_SUBDIR / filename

    expected = _expected_artifact_names(spec)

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
        validate_artifacts=_validate_statement_artifacts(expected),
        generated_artifacts=GeneratedArtifacts(
            artifacts=_example_inputs(spec),
            stale=_stale_example_inputs(problem_id, spec, problems_dir=problems_dir),
            reason="the input of a sample is copied from the spec by the orchestrator",
        ),
    )
