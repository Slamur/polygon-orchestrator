"""Согласованность учебных задач-образцов в `templates/examples/`.

`spec.yaml`, `constraints.yaml` и `validator.cpp` каждой учебной задачи
прикладываются к вызовам модели как пара "спек -> ожидаемый результат" (см.
docs/PROMPTS.md, "Приложенные шаблоны как образцы"). Если образцы разойдутся
между собой, модель начнёт учиться неверному соответствию — тесты ловят то,
что можно проверить механически. Каждый тест прогоняется по всем каталогам
`templates/examples/*`, так что новая учебная задача проверяется без правки
этого файла.
"""

import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from orchestrator.checks.compile_check import compile_check
from orchestrator.polygon.steps.set_constraints import _extract_limits
from orchestrator.spec import load_spec
from orchestrator.references import SPEC_FILENAME
from orchestrator.steps.base import COMMON_SUBDIR, EXAMPLES_SUBDIR
from orchestrator.steps.generators_and_script import SCRIPT_FUNCTIONS_FILENAME, SCRIPT_NAMES

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
EXAMPLE_DIRS = sorted(path for path in (TEMPLATES_DIR / EXAMPLES_SUBDIR).iterdir() if path.is_dir())
EXAMPLE_CPP_FILES = sorted(path for example in EXAMPLE_DIRS for path in example.rglob("*.cpp"))


def _id(path: Path) -> str:
    return path.relative_to(TEMPLATES_DIR / EXAMPLES_SUBDIR).as_posix()


examples = pytest.mark.parametrize("example_dir", EXAMPLE_DIRS, ids=_id)


def _example_spec(example_dir: Path):
    return load_spec(example_dir / SPEC_FILENAME, expected_id=example_dir.name)


def _example_constraints(example_dir: Path) -> dict:
    return yaml.safe_load((example_dir / "constraints.yaml").read_text(encoding="utf-8"))


def test_examples_exist():
    assert EXAMPLE_DIRS


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
        yaml.safe_load((TEMPLATES_DIR / COMMON_SUBDIR / "constraints_format.yaml").read_text(encoding="utf-8"))
    )
    constraints_keys = set(_example_constraints(example_dir))
    assert constraints_keys <= format_keys
    assert format_keys - {"test_groups"} <= constraints_keys


@examples
def test_example_constraints_limits_are_readable_by_set_constraints(example_dir):
    spec = _example_spec(example_dir)
    limits = _extract_limits(_example_constraints(example_dir))
    assert limits == (spec.constraints.time_limit_seconds, spec.constraints.memory_limit_mb)


@examples
def test_example_validator_uses_constraints_bounds(example_dir):
    validator = (example_dir / "validator.cpp").read_text(encoding="utf-8")
    declared = {
        name: float(value)
        for name, value in re.findall(r"\b((?:min|max)\w+)\s*=\s*([0-9.e]+)", validator)
    }
    bounds = sorted(
        float(bound)
        for constant in _example_constraints(example_dir)["validator"]["constants"]
        for bound in (constant["min"], constant["max"])
    )
    assert sorted(declared.values()) == bounds


@examples
def test_example_test_scripts_contain_standard_functions(example_dir):
    # Шаблон стандартных функций приложен к generators_and_script всегда, а
    # скрипт-образец — через референсы: если в образце окажется другой набор,
    # модель получит два противоречащих друг другу образца.
    functions = (TEMPLATES_DIR / COMMON_SUBDIR / SCRIPT_FUNCTIONS_FILENAME).read_text(encoding="utf-8")
    for name in SCRIPT_NAMES:
        script_path = example_dir / name
        if script_path.exists():
            assert functions in script_path.read_text(encoding="utf-8"), script_path


def _compile(cpp_path: Path) -> str | None:
    """stderr компилятора, если `cpp_path` не собрался, иначе None."""
    result = compile_check(cpp_path, TEMPLATES_DIR / COMMON_SUBDIR)
    if result.binary_path:
        result.binary_path.unlink(missing_ok=True)
    return None if result.success else result.stderr


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
def test_example_cpp_files_compile():
    # Параллельно: с testlib.h каждый файл собирается несколько секунд.
    with ThreadPoolExecutor() as pool:
        errors = dict(zip(EXAMPLE_CPP_FILES, pool.map(_compile, EXAMPLE_CPP_FILES)))
    failed = {_id(path): stderr for path, stderr in errors.items() if stderr is not None}
    assert EXAMPLE_CPP_FILES
    assert not failed, failed
