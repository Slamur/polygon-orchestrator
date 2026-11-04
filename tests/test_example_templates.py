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
from compile_cache import source_key

from orchestrator.checks.compile_check import compile_check
from orchestrator.limits import LIMITS_FILENAME, extract_limits, parse_limits
from orchestrator.problem_paths import (
    AUTHORING_DIRNAME,
    EXAMPLES_DIR,
    SPEC_FILENAME,
    iter_problem_dirs,
    problem_id_for,
)
from orchestrator.spec import load_spec
from orchestrator.steps.generators_draft import generator_params
from orchestrator.steps.test_script_draft import (
    SCRIPT_FUNCTIONS_FILENAME,
    SCRIPT_NAMES,
    fix_script_comparisons,
)
from orchestrator.steps.tests_plan import (
    parse_tests_plan,
    plan_generators,
    uncovered_notes,
)

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"
EXAMPLES_ROOT = REPO_ROOT / EXAMPLES_DIR
EXAMPLE_DIRS = sorted(iter_problem_dirs(EXAMPLES_ROOT))


def _id(path: Path) -> str:
    return path.relative_to(EXAMPLES_ROOT).as_posix()


examples = pytest.mark.parametrize("example_dir", EXAMPLE_DIRS, ids=_id)


def _example_spec(example_dir: Path):
    expected_id = problem_id_for(EXAMPLES_ROOT, _id(example_dir))
    return load_spec(example_dir / AUTHORING_DIRNAME / SPEC_FILENAME, expected_id=expected_id)


def _example_constraints(example_dir: Path) -> dict:
    return yaml.safe_load(
        (example_dir / AUTHORING_DIRNAME / "constraints.yaml").read_text(encoding="utf-8")
    )


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
    assert [
        (c["name"], c["min"], c["max"]) for c in constraints["validator"]["constants"]
    ] == expected


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
def test_example_limits_file_is_readable(example_dir):
    # С constraints.yaml не сравнивается: актуальные TL/ML могут отличаться от
    # предложенных constraints_pick (подгонка на Polygon, `polygon pull`).
    path = example_dir / LIMITS_FILENAME
    assert path.is_file(), f"{path} is missing"
    parse_limits(path.read_text(encoding="utf-8"))


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
    # Шаблон стандартных функций приложен к test_script_draft всегда, а
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


@examples
def test_example_tests_plan_matches_spec_and_generators(example_dir):
    # Пара "спек -> план" для tests_plan и "план -> генераторы" для
    # generators_draft: план разбирается, покрывает все пункты
    # specific_test_ideas/known_wrong_approaches спека, и его генераторы — это
    # ровно generators/*.cpp с теми же параметрами opt<T>(...).
    plan = parse_tests_plan(
        (example_dir / AUTHORING_DIRNAME / "tests_plan.yaml").read_text(encoding="utf-8")
    )
    spec = _example_spec(example_dir)
    assert plan["problem_id"] == spec.problem_id
    assert uncovered_notes(plan, spec) == []
    planned = {
        g["name"]: sorted(p["name"] for p in g.get("params") or []) for g in plan_generators(plan)
    }
    actual = {
        path.stem: sorted(generator_params(path.read_text(encoding="utf-8")))
        for path in (example_dir / "generators").glob("*.cpp")
    }
    assert planned == actual


# Таймаут сборки образца: пул собирает в фоне, пока остальные тесты тоже
# компилируют, и на занятой машине 30 секунд по умолчанию не хватает.
_EXAMPLE_COMPILE_TIMEOUT_SECONDS = 120.0


def _compile(cpp_path: Path, cache_dir: Path | None) -> str | None:
    """stderr компилятора, если `cpp_path` не собрался, иначе None.

    `problem_lib.h` ищется, как в compile-check пайплайна, сначала в каталоге
    задачи (у образца может быть своя копия с функциями задачи), потом в
    `templates/`. Удачная сборка помечается в `cache_dir` по `source_key`
    (исходник после препроцессора, флаги, версия g++): тот же вход между
    запусками pytest не пересобирается, изменённый собирается честно."""
    example_dir = next(example for example in EXAMPLE_DIRS if example in cpp_path.parents)
    include_dirs = [example_dir, TEMPLATES_DIR]
    key = source_key(cpp_path, include_dirs) if cache_dir else None
    marker = cache_dir / key if cache_dir and key else None
    if marker is not None and marker.exists():
        return None
    result = compile_check(cpp_path, include_dirs, timeout_seconds=_EXAMPLE_COMPILE_TIMEOUT_SECONDS)
    if result.binary_path:
        result.binary_path.unlink(missing_ok=True)
    if not result.success:
        return result.stderr
    if marker is not None:
        marker.touch()
    return None


@pytest.fixture(scope="session")
def _example_compilations(request, pytestconfig):
    """Сборки `.cpp` выбранных образцов, запущенные сразу все параллельно.

    Тест на образец ждёт только свои файлы, но собираются они в общем пуле:
    с testlib.h каждый файл собирается несколько секунд, а у одного образца
    файлов меньше, чем ядер. Компиляций — не больше половины ядер: остальные
    тесты в это время тоже собирают и запускают бинари.
    Кэш — в `.pytest_cache` (без cacheprovider — без кэша)."""
    cache = getattr(pytestconfig, "cache", None)
    cache_dir = cache.mkdir("example-compiles") if cache else None
    selected = sorted(
        {
            item.callspec.params["example_dir"]
            for item in request.session.items
            if getattr(item, "originalname", None) == test_example_cpp_files_compile.__name__
        }
    )
    with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2)) as pool:
        yield {
            path: pool.submit(_compile, path, cache_dir)
            for example_dir in selected
            for path in sorted(example_dir.rglob("*.cpp"))
        }


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
@examples
def test_example_cpp_files_compile(example_dir, _example_compilations):
    cpp_files = sorted(example_dir.rglob("*.cpp"))
    failed = {
        path.relative_to(example_dir).as_posix(): stderr
        for path in cpp_files
        if (stderr := _example_compilations[path].result()) is not None
    }
    assert cpp_files
    assert not failed, failed
