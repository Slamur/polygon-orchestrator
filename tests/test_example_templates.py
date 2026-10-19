"""Согласованность учебных образцов в `templates/`.

`example_spec.yaml`, `constraints.yaml` и `validator.cpp` прикладываются к
вызовам модели как пара "спек -> ожидаемый результат" (см. docs/PROMPTS.md,
"Приложенные шаблоны как образцы"). Если образцы разойдутся между собой,
модель начнёт учиться неверному соответствию — тесты ловят то, что можно
проверить механически.
"""

import re
from pathlib import Path

import yaml

from orchestrator.polygon.steps.set_constraints import _extract_limits
from orchestrator.spec import load_spec

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _example_spec():
    return load_spec(TEMPLATES_DIR / "example_spec.yaml")


def _example_constraints() -> dict:
    return yaml.safe_load((TEMPLATES_DIR / "constraints.yaml").read_text(encoding="utf-8"))


def test_example_spec_is_a_valid_spec():
    spec = _example_spec()
    assert spec.solutions is not None
    assert spec.checker.custom_needed is True


def test_example_constraints_match_example_spec():
    spec = _example_spec()
    constraints = _example_constraints()

    assert constraints["problem_id"] == spec.problem_id
    assert constraints["intended_complexity"] == spec.constraints.intended_complexity
    expected = [(v.name, v.min, v.max) for v in spec.constraints.variables]
    assert [(v["name"], v["min"], v["max"]) for v in constraints["variables"]] == expected
    assert [(c["name"], c["min"], c["max"]) for c in constraints["validator"]["constants"]] == expected


def test_example_constraints_limits_are_readable_by_set_constraints():
    spec = _example_spec()
    limits = _extract_limits(_example_constraints())
    assert limits == (spec.constraints.time_limit_seconds, spec.constraints.memory_limit_mb)


def test_example_validator_uses_constraints_bounds():
    validator = (TEMPLATES_DIR / "validator.cpp").read_text(encoding="utf-8")
    declared = {
        name: float(value)
        for name, value in re.findall(r"\b((?:min|max)\w+)\s*=\s*([0-9.e]+)", validator)
    }
    bounds = sorted(
        float(bound)
        for constant in _example_constraints()["validator"]["constants"]
        for bound in (constant["min"], constant["max"])
    )
    assert sorted(declared.values()) == bounds
