"""Шаг 3, третья часть — FreeMarker test-script по плану тестов, CLAUDE.md, "Роль каждого
генеративного шага", пункт 3.

Механическая часть: блоки `tests` плана (`authoring/tests_plan.yaml`, шаг
`tests_plan`) с его `constants` — в `test_script` (`generation.script_style:
flat`) или `test_script_groups` (`groups`). Генераторы шаг видит только по
плану — имя и параметры, без кода: скрипт вызывает их так, как их описал план,
и правка кода генератора скрипт не инвалидирует.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Optional

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
    MissingArtifactsError,
    StepResult,
    StepRevision,
    artifact_filename,
    constraints_without_limits,
    existing_artifacts,
    require_constraints_yaml,
    run_generative_step,
)
from orchestrator.steps.tests_plan import (
    TESTS_PLAN_ARTIFACT,
    dump_plan_part,
    load_tests_plan,
    plan_generators,
)

STEP_NAME = "test_script_draft"

# Стандартные функции test-script'а (div, pow, fact, ...) — шаблон автора в
# templates/. Модель обязана копировать блок целиком (prompts/
# test_script_draft/system.md), но на практике выкидывает неиспользуемые
# функции, а автору они нужны под рукой при ручной правке скрипта, —
# поэтому выкинутые дописываются механически (`restore_script_functions`).
SCRIPT_FUNCTIONS_FILENAME = "test_script_functions.ftl"
SCRIPT_NAMES = ("test_script", "test_script_groups")
_SCRIPT_SECTION_MARKER = '<#assign desc = "{}"/>'
_FUNCTION_RE = re.compile(r"<#function\s+(\w+)\b.*?</#function>", re.S)

def _script_functions(script: str) -> dict[str, str]:
    """Имя функции -> её определение `<#function ...>...</#function>`."""
    return {m.group(1): m.group(0) for m in _FUNCTION_RE.finditer(script)}


def restore_script_functions(script: str, functions_block: str) -> tuple[str, list[str]]:
    """Дописывает в `script` функции из `functions_block`, которых в нём нет
    (по имени; функцию, которую модель переписала, не трогает). Куда:
    - после маркера секции `<#assign desc = "Functions"/>`, если он есть;
    - иначе перед первой функцией скрипта, если она есть;
    - иначе весь блок с маркером — перед секцией "Constraints", а без неё —
      в начало скрипта (определения функций в FreeMarker видны во всём
      шаблоне, так что порядок на работу скрипта не влияет).
    Возвращает новый скрипт и имена дописанных функций (в порядке блока).
    """
    present = _script_functions(script)
    missing = [(name, body) for name, body in _script_functions(functions_block).items()
               if name not in present]
    if not missing:
        return script, []
    names = [name for name, _ in missing]
    bodies = "\n".join(body for _, body in missing) + "\n"

    functions_marker = _SCRIPT_SECTION_MARKER.format("Functions")
    marker_at = script.find(functions_marker)
    if marker_at != -1:
        insert_at = marker_at + len(functions_marker)
        return script[:insert_at] + "\n" + bodies.rstrip("\n") + script[insert_at:], names
    first_function = _FUNCTION_RE.search(script)
    if first_function:
        insert_at = first_function.start()
        return script[:insert_at] + bodies + script[insert_at:], names
    block = functions_marker + "\n" + bodies + "\n"
    constraints_at = script.find(_SCRIPT_SECTION_MARKER.format("Constraints"))
    insert_at = 0 if constraints_at == -1 else constraints_at
    return script[:insert_at] + block + script[insert_at:], names


# Директива FreeMarker, занимающая хвост строки: `<#if value >= n>`. Внутри
# тега `>` закрывает тег (`<#if value>` + текст `= n>`), поэтому сравнения
# пишутся словами (`gt`/`gte`/`lt`/`lte`, templates/tutorials/freemarker.md),
# но модель время от времени пишет знаки. Границы выражения берутся по концу
# строки, а не по первому `>`, — иначе его не восстановить.
_DIRECTIVE_LINE_RE = re.compile(
    r"(<#(?:if|elseif|assign|local|global|return)\s)((?:(?!<[#/@]).)*?)(\s*/?>[ \t]*)$", re.M
)
_STRING_RE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
# `->` (лямбда) и `..<` (диапазон) — не сравнения.
_COMPARISON_RE = re.compile(r"\s*(?<![-.])(>=|<=|>|<)\s*")
_COMPARISON_WORDS = {">=": "gte", "<=": "lte", ">": "gt", "<": "lt"}


def _replace_comparisons(expression: str) -> tuple[str, int]:
    parts: list[str] = []
    count = 0
    position = 0
    for string in [*_STRING_RE.finditer(expression), None]:
        end = string.start() if string else len(expression)
        code, replaced = _COMPARISON_RE.subn(
            lambda m: f" {_COMPARISON_WORDS[m.group(1)]} ", expression[position:end]
        )
        parts.append(code)
        count += replaced
        if string:
            parts.append(string.group(0))
            position = string.end()
    return "".join(parts), count


def fix_script_comparisons(script: str) -> tuple[str, int]:
    """Заменяет знаки сравнения в директивах скрипта на `gt`/`gte`/`lt`/`lte`
    (см. `_DIRECTIVE_LINE_RE`). Только директивы, которые заканчиваются в
    конце своей строки; строковые литералы не трогает. Возвращает новый
    скрипт и число замен."""
    total = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal total
        expression, count = _replace_comparisons(match.group(2))
        total += count
        return match.group(1) + expression + match.group(3)

    return _DIRECTIVE_LINE_RE.sub(replace, script), total


def _restore_functions_in_artifacts(
    functions_block: str,
) -> Callable[[dict[str, str]], tuple[dict[str, str], list[dict[str, Any]]]]:
    """`postprocess_artifacts` для `run_generative_step`: прогоняет каждый
    test-script ответа через `fix_script_comparisons` и
    `restore_script_functions` и оставляет заметку по каждому, где что-то
    исправлено или дописано."""

    def postprocess(artifacts: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
        result = dict(artifacts)
        notes: list[dict[str, Any]] = []
        for name in SCRIPT_NAMES:
            if name not in result:
                continue
            result[name], fixed = fix_script_comparisons(result[name])
            if fixed:
                notes.append({
                    "field": name,
                    "kind": "restored",
                    "explanation": (
                        f"знаки сравнения внутри директив FreeMarker ({fixed} шт.) заменены "
                        "оркестратором на gt/gte/lt/lte: `>` внутри тега закрывает тег"
                    ),
                })
            result[name], restored = restore_script_functions(result[name], functions_block)
            if restored:
                notes.append({
                    "field": name,
                    "kind": "restored",
                    "explanation": (
                        f"модель не скопировала стандартные функции {', '.join(restored)} "
                        f"из {SCRIPT_FUNCTIONS_FILENAME} — дописаны оркестратором"
                    ),
                })
        return result, notes

    return postprocess


def _generator_signatures(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Генераторы плана без `algorithm`/`rationale`: скрипту нужно только,
    как их вызывать."""
    keys = ("name", "params", "input", "output")
    return [{key: generator.get(key) for key in keys} for generator in plan_generators(plan)]


def script_name(spec: ProblemSpec) -> str:
    return "test_script" if spec.generation.script_style == "flat" else "test_script_groups"


def hash_inputs(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> tuple[Any, Optional[dict[str, Any]]]:
    """`(секция спека, апстрим-артефакты)`: `generation.script_style`;
    `constraints.yaml` без TL/ML (ограничения и группы скрипта), из плана —
    `constants`, `tests` и сигнатуры генераторов (`_generator_signatures`)."""
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    return {"generation": {"script_style": spec.generation.script_style}}, {
        CONSTRAINTS_ARTIFACT: constraints_without_limits(constraints_yaml),
        f"{TESTS_PLAN_ARTIFACT}: generators": dump_plan_part(_generator_signatures(plan)),
        f"{TESTS_PLAN_ARTIFACT}: constants": dump_plan_part(plan.get("constants") or []),
        f"{TESTS_PLAN_ARTIFACT}: tests": dump_plan_part(plan.get("tests")),
    }


def compute_input_hash(
    problem_id: str,
    spec: ProblemSpec,
    upstream_artifacts: Optional[dict[str, Any]] = None,
) -> str:
    return _cache_compute_input_hash(STEP_NAME, *hash_inputs(problem_id, spec, upstream_artifacts))


def extra_context_documents(
    spec: ProblemSpec, *, templates_dir: Path = TEMPLATES_DIR
) -> Optional[list[str]]:
    """Образцы — test-script'ы из референсов спека (`orchestrator/references.py`)."""
    return reference_documents(STEP_NAME, spec)


def primary_artifact_path(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> Path:
    """Путь к test-script'у: имя зависит от `generation.script_style`
    (CLAUDE.md, "Структура каталогов")."""
    return Path(problems_dir) / problem_id / script_name(spec)


def current_artifacts(
    problem_id: str, spec: ProblemSpec, *, problems_dir: Path = PROBLEMS_DIR
) -> dict[str, Path]:
    """Текущие файлы шага для режима доработки: оба test-script'а, какие есть."""
    problem_dir = Path(problems_dir) / problem_id
    return existing_artifacts({name: problem_dir / name for name in SCRIPT_NAMES})


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
    """Прогоняет шаг `test_script_draft` для `problem_id`.

    Вход: `constants`, `tests` и сигнатуры генераторов плана,
    `constraints.yaml` без TL/ML — единственный источник диапазонов и групп,
    `generation.script_style`. Выход — `test_script` или `test_script_groups`
    в каталоге задачи; знаки сравнения в директивах и выкинутые моделью
    стандартные функции исправляет оркестратор (`_restore_functions_in_artifacts`).
    """
    constraints_yaml = require_constraints_yaml(STEP_NAME, problem_id, upstream_artifacts)
    plan = load_tests_plan(STEP_NAME, problem_id, upstream_artifacts)
    name = script_name(spec)
    context = {
        "problem_id": spec.problem_id,
        "script_name": name,
        "script_style": spec.generation.script_style,
        "constraints_yaml": constraints_without_limits(constraints_yaml),
        "generators": dump_plan_part(_generator_signatures(plan)),
        "constants": dump_plan_part(plan.get("constants") or []),
        "tests": dump_plan_part(plan.get("tests")),
    }
    input_hash = compute_input_hash(problem_id, spec, upstream_artifacts)
    input_values = compute_input_values(*hash_inputs(problem_id, spec, upstream_artifacts))

    def resolve_artifact_path(filename: str) -> Path:
        filename = artifact_filename(filename)
        if filename not in SCRIPT_NAMES:
            raise ValueError(
                f"Step '{STEP_NAME}' returns only {' or '.join(SCRIPT_NAMES)}, got '{filename}'"
            )
        return Path(problems_dir) / problem_id / filename

    def validate_artifacts(artifacts: dict[str, str]) -> None:
        if name not in artifacts:
            raise MissingArtifactsError(f"Step '{STEP_NAME}': the response has no {name}", [name])

    return run_generative_step(
        step_name=STEP_NAME,
        problem_id=problem_id,
        context=context,
        input_hash=input_hash,
        input_values=input_values,
        revision=revision,
        saved_response=saved_response,
        resolve_artifact_path=resolve_artifact_path,
        validate_artifacts=validate_artifacts,
        # Файлы независимы: посторонний файл в ответе не мешает скрипту.
        partial_apply=True,
        prompts_dir=prompts_dir,
        problems_dir=problems_dir,
        templates_dir=templates_dir,
        extra_context_documents=extra_context_documents(spec, templates_dir=templates_dir),
        postprocess_artifacts=_restore_functions_in_artifacts(
            (Path(templates_dir) / SCRIPT_FUNCTIONS_FILENAME).read_text(encoding="utf-8")
        ),
    )
