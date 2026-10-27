"""Согласованность учебных задач-образцов в `examples/`.

`authoring/spec.yaml`, `authoring/constraints.yaml` и `validator.cpp` каждой учебной задачи
прикладываются к вызовам модели как пара "спек -> ожидаемый результат" (см.
docs/PROMPTS.md, "Приложенные шаблоны как образцы"). Если образцы разойдутся
между собой, модель начнёт учиться неверному соответствию — тесты ловят то,
что можно проверить механически. Каждый тест прогоняется по всем каталогам
задач в `examples/` (на любой глубине, см. `orchestrator/problem_paths.py`),
так что новая учебная задача проверяется без правки этого файла.
"""

import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from orchestrator.checks.compile_check import compile_check
from orchestrator.limits import extract_limits
from orchestrator.problem_paths import iter_problem_dirs, problem_id_for
from orchestrator.spec import load_spec
from orchestrator.problem_paths import AUTHORING_DIRNAME, SPEC_FILENAME
from orchestrator.steps.base import EXAMPLES_DIR
from orchestrator.steps.generators_and_script import (
    SCRIPT_FUNCTIONS_FILENAME,
    SCRIPT_NAMES,
    fix_script_comparisons,
)

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
EXAMPLES_ROOT = REPO_ROOT / EXAMPLES_DIR
EXAMPLE_DIRS = sorted(iter_problem_dirs(EXAMPLES_ROOT))
EXAMPLE_CPP_FILES = sorted(path for example in EXAMPLE_DIRS for path in example.rglob("*.cpp"))


def _id(path: Path) -> str:
    return path.relative_to(EXAMPLES_ROOT).as_posix()


examples = pytest.mark.parametrize("example_dir", EXAMPLE_DIRS, ids=_id)


def _example_spec(example_dir: Path):
    expected_id = problem_id_for(EXAMPLES_ROOT, _id(example_dir))
    return load_spec(example_dir / AUTHORING_DIRNAME / SPEC_FILENAME, expected_id=expected_id)


def _example_constraints(example_dir: Path) -> dict:
    return yaml.safe_load((example_dir / AUTHORING_DIRNAME / "constraints.yaml").read_text(encoding="utf-8"))


def test_examples_exist():
    assert EXAMPLE_DIRS


def test_example_dirs_hold_only_problems():
    """Файлы образцов лежат только в каталогах задач: всё, что вне них,
    не проверяется этими тестами и не годится как референс."""
    stray = [
        path
        for path in EXAMPLES_ROOT.rglob("*")
        if path.is_file() and not any(example in path.parents for example in EXAMPLE_DIRS)
    ]
    assert stray == []


@examples
def test_example_spec_is_a_valid_spec(example_dir):
    spec = _example_spec(example_dir)
    if (example_dir / "checker.cpp").exists():
        assert spec.checker.custom_needed is True
    if (example_dir / "solutions").exists():
        assert spec.solutions is not None


@examples
def test_example_constraints_match_example_spec(example_dir):
    spec = _example_spec(example_dir)
    constraints = _example_constraints(example_dir)

    assert constraints["problem_id"] == spec.problem_id
    assert constraints["intended_complexity"] == spec.constraints.intended_complexity
    expected = [(v.name, v.min, v.max) for v in spec.constraints.variables]
    assert [(v["name"], v["min"], v["max"]) for v in constraints["variables"]] == expected
    assert [(c["name"], c["min"], c["max"]) for c in constraints["validator"]["constants"]] == expected


@examples
def test_example_constraints_follow_constraints_format(example_dir):
    # constraints_format.yaml прикладывается к constraints_pick как формат
    # результата — образцы не должны ему противоречить.
    format_keys = set(
        yaml.safe_load((TEMPLATES_DIR / "constraints_format.yaml").read_text(encoding="utf-8"))
    )
    constraints_keys = set(_example_constraints(example_dir))
    assert constraints_keys <= format_keys
    assert format_keys - {"test_groups"} <= constraints_keys


@examples
def test_example_constraints_limits_are_readable_by_set_constraints(example_dir):
    spec = _example_spec(example_dir)
    limits = extract_limits(_example_constraints(example_dir))
    assert limits == (spec.constraints.time_limit_seconds, spec.constraints.memory_limit_mb)


@examples
def test_example_validator_uses_constraints_bounds(example_dir):
    validator = (example_dir / "validator.cpp").read_text(encoding="utf-8")
    declared = {
        name: float(value)
        for name, value in re.findall(r"\b((?:min|max)\w+)\s*=\s*(-?[0-9.e]+)", validator)
    }
    # Границы-выражения (`max: N`) валидатор берёт из уже прочитанных
    # значений, а не из констант — сравниваются только числовые.
    bounds = sorted(
        float(bound)
        for constant in _example_constraints(example_dir)["validator"]["constants"]
        for bound in (constant["min"], constant["max"])
        if not isinstance(bound, str)
    )
    assert sorted(declared.values()) == bounds


@examples
def test_example_test_scripts_contain_standard_functions(example_dir):
    # Шаблон стандартных функций приложен к generators_and_script всегда, а
    # скрипт-образец — через референсы: если в образце окажется другой набор,
    # модель получит два противоречащих друг другу образца.
    functions = (TEMPLATES_DIR / SCRIPT_FUNCTIONS_FILENAME).read_text(encoding="utf-8")
    for name in SCRIPT_NAMES:
        script_path = example_dir / name
        if script_path.exists():
            assert functions in script_path.read_text(encoding="utf-8"), script_path


@examples
def test_example_test_scripts_have_no_comparison_signs_in_directives(example_dir):
    # `>` внутри тега FreeMarker закрывает тег — Polygon такой скрипт не
    # сохраняет (templates/tutorials/freemarker.md, "Сравнения").
    for name in SCRIPT_NAMES:
        script_path = example_dir / name
        if script_path.exists():
            _, fixed = fix_script_comparisons(script_path.read_text(encoding="utf-8"))
            assert fixed == 0, script_path


def _compile(cpp_path: Path) -> str | None:
    """stderr компилятора, если `cpp_path` не собрался, иначе None.

    `problem_lib.h` ищется, как в compile-check пайплайна, сначала в каталоге
    задачи (у образца может быть своя копия с функциями задачи), потом в
    `templates/`."""
    example_dir = next(example for example in EXAMPLE_DIRS if example in cpp_path.parents)
    result = compile_check(cpp_path, [example_dir, TEMPLATES_DIR])
    if result.binary_path:
        result.binary_path.unlink(missing_ok=True)
    return None if result.success else result.stderr


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
def test_example_cpp_files_compile():
    # Параллельно: с testlib.h каждый файл собирается несколько секунд; не
    # больше компиляций, чем ядер, иначе при десятках файлов отдельные
    # компиляции упираются в таймаут compile_check.
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        errors = dict(zip(EXAMPLE_CPP_FILES, pool.map(_compile, EXAMPLE_CPP_FILES)))
    failed = {_id(path): stderr for path, stderr in errors.items() if stderr is not None}
    assert EXAMPLE_CPP_FILES
    assert not failed, failed
