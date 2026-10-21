"""Шаг 4 — черновики авторских решений (ok/wa/tl), CLAUDE.md, "Роль каждого
генеративного шага", пункт 4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from orchestrator.cache import compute_input_hash as _cache_compute_input_hash
from orchestrator.spec import ProblemSpec
from orchestrator.steps.base import (
    DEFAULT_EXAMPLE_SUBDIR,
    OUTPUTS_DIR,
    PROMPTS_DIR,
    TEMPLATES_DIR,
    StepResult,
    run_generative_step,
    statement_io_section,
    with_default_lists,
)
from orchestrator.steps.generators_and_script import (
    CONSTRAINTS_ARTIFACT,
    _require_constraints_yaml,
)

STEP_NAME = "solutions_draft"

# Optional[list[...]]-поле секции solutions, по которому user.md.j2 делает
# {% for %} без проверки на null (см. base.with_default_lists).
_LIST_FIELDS = ["known_wrong_approaches"]

# Каталог решений учебной задачи внутри templates_dir, см. `extra_context_documents`.
SOLUTION_TEMPLATES_SUBDIR = f"{DEFAULT_EXAMPLE_SUBDIR}/solutions"

# `solutions.languages` — свободные строки; приводим распространённые
# варианты записи к тому, как язык назван в именах файлов шаблонов.
_LANGUAGE_ALIASES = {"c++": "cpp", "python": "py", "python3": "py"}


def _require_solutions_section(problem_id: str, spec: ProblemSpec) -> None:
    if spec.solutions is None:
        raise ValueError(
            f"Step '{STEP_NAME}' for '{problem_id}': the spec has no "
            "'solutions' section — solution drafts not requested, the step is not run "
            "(see SPEC_FORMAT.md, section 4 — optional)"
        )


def _build_context_sections(spec: ProblemSpec) -> dict[str, Any]:
    """Секции спека для рендера промпта И для входа хеша кэша — единая точка
    сборки, чтобы они не разошлись (CLAUDE.md, "Кэширование по хешу спека"):
    секция `solutions`, `constraints.intended_complexity` и условие из
    `statement_draft` целиком в части ввода-вывода (`formal_input_sketch`,
    `formal_output_sketch`, `indexing`, примеры с ответами, легенда —
    `base.statement_io_section`). Без последнего модели неоткуда взять
    формат ввода-вывода своей задачи, кроме формата учебной из
    `spec.yaml`.
    """
    return {
        "solutions": with_default_lists(spec.solutions.model_dump(mode="json"), _LIST_FIELDS),
        "constraints": {"intended_complexity": spec.constraints.intended_complexity},
        "statement": statement_io_section(spec, include_output_and_legend=True),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    """Хеш входа шага без вызова модели, см. `statement_draft.compute_input_hash`
    и CLAUDE.md, "Кэширование по хешу спека". Как и `run_step`, требует
    секцию `solutions` в спеке и `upstream_artifacts["constraints.yaml"]`.
    Вход — `_build_context_sections` + содержимое `constraints.yaml`.
    """
    _require_solutions_section(problem_id, spec)
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    return _cache_compute_input_hash(
        STEP_NAME,
        _build_context_sections(spec),
        upstream_artifacts={CONSTRAINTS_ARTIFACT: constraints_yaml},
    )


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, outputs_dir: Path = OUTPUTS_DIR
) -> Path:
    """Каталог с черновиками решений — точный список файлов модель выбирает
    сама (зависит от `solutions.wanted_verdicts`), поэтому в отличие от
    остальных шагов здесь нет фиксированного имени файла: `pipeline.py`
    считает кэш валидным, только если каталог существует и не пуст.
    """
    return Path(outputs_dir) / problem_id / "solutions"


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Решения учебной задачи (`SOLUTION_TEMPLATES_SUBDIR`) на языках, заказанных в
    `solutions.languages`, — образец структуры решения и ввода/вывода для
    каждого языка (см. `prompts/solutions_draft/system.md`). Язык шаблона —
    второй компонент имени файла по схеме `<verdict>_<language>_...`
    (`ok_cpp_prefs_draft.cpp` -> `cpp`); шаблоны на незаказанных языках не
    прикладываются, чтобы не тянуть в контекст чужой стиль ввода/вывода.

    В отличие от фиксированного `STEP_CONTEXT_DOCUMENTS`, набор зависит от
    спека, поэтому идёт через `extra_context_documents` (и, как и они, входит
    в `compute_prompt_hash`). Если секции `solutions` нет или для заказанных
    языков шаблонов нет — `None`: шаг работает без примеров, как и раньше.
    """
    if spec.solutions is None:
        return None

    languages = {_LANGUAGE_ALIASES.get(lang.lower(), lang.lower()) for lang in spec.solutions.languages}
    solutions_dir = Path(templates_dir) / SOLUTION_TEMPLATES_SUBDIR
    paths = sorted(
        str(path)
        for path in solutions_dir.glob("*")
        if path.is_file() and _template_language(path) in languages
    )
    return paths or None


def _template_language(path: Path) -> Optional[str]:
    parts = path.stem.split("_")
    return parts[1] if len(parts) >= 2 else None


def run_step(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
    *,
    prompts_dir: Path = PROMPTS_DIR,
    outputs_dir: Path = OUTPUTS_DIR,
    templates_dir: Path = TEMPLATES_DIR,
) -> StepResult:
    """Прогоняет шаг `solutions_draft` для `problem_id`.

    Вход: секция `solutions` спека (`algorithm_hint`, `wanted_verdicts`,
    `languages`, `known_wrong_approaches`) плюс зафиксированная асимптотика
    из `constraints.intended_complexity` — она уже провалидирована как
    непустая (`orchestrator.spec.Constraints`) и не пересчитывается заново,
    только передаётся в промпт как заданный факт. Плюс условие из
    `statement_draft` в части ввода-вывода (формат ввода и вывода,
    индексация, примеры с ответами, легенда — см. `_build_context_sections`)
    и полный текст апстрим-артефакта `constraints_pick`
    (`upstream_artifacts["constraints.yaml"]`: диапазоны переменных, TL/ML,
    гарантии валидатора) — без них модель вынуждена угадывать формат
    ввода-вывода и типы величин.

    Требует, чтобы в спеке была включена опциональная секция `solutions`
    (`spec.solutions is not None`) — без неё черновики решений не
    заказывались автором, и шаг не имеет смысла запускать.

    Модель обязана взять алгоритм ТОЛЬКО из `solutions.algorithm_hint` и не
    имеет права изобретать другой алгоритм или менять заявленную
    асимптотику (CLAUDE.md, "Роль каждого генеративного шага", п.4: "черновики
    решений с заявленными вердиктами... это не финальные решения", и
    `docs/PROMPTS.md`, правило 1 для `solutions_draft`: "не изобретай другой
    алгоритм"). Если `algorithm_hint` пуст или слишком расплывчат — `status:
    uncertain`, и вызов завершится `StepUncertainError` без записи файлов, а
    не попыткой домыслить алгоритм по легенде/условию.

    При успехе пишет файлы решений в `outputs/<problem_id>/solutions/`.
    """
    _require_solutions_section(problem_id, spec)
    constraints_yaml = _require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)

    context = {
        "problem_id": problem_id,
        **_build_context_sections(spec),
        "constraints_pick_result": {"artifacts": {CONSTRAINTS_ARTIFACT: constraints_yaml}},
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)

    def resolve_artifact_path(filename: str) -> Path:
        return Path(outputs_dir) / problem_id / "solutions" / filename

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        resolve_artifact_path=resolve_artifact_path,
        prompts_dir=prompts_dir,
        outputs_dir=outputs_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
    )
