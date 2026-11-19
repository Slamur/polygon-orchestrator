"""The test design (`orchestrator/steps/tests_design.py`): parsing, formulas, building the plan."""

import pytest
import yaml

from orchestrator.steps.tests_design import (
    PLAN_HEADER,
    TestsDesignError,
    build_tests_plan,
    parse_limits,
    parse_tests_design,
    plan_from_design,
)
from orchestrator.steps.tests_plan import parse_tests_plan

CONSTRAINTS = """\
variables:
  - {name: n, min: 1, max: 100000, description: the number of elements}
  - {name: a, min: 1, max: 1000000000, description: an element}
test_groups:
  - name: "1"
    variables: {n: {max: 10}, a: {max: 1000}}
    points: 20
  - name: "2"
    variables: {n: {max: 1000}}
    points: 30
    dependencies: ["1"]
  - name: "3"
    points: 50
    dependencies: ["2"]
"""

FLAT_CONSTRAINTS = """\
variables:
  - {name: n, min: 1, max: 100000, description: the number of elements}
"""

GENERATORS = [
    {
        "name": "gen_rand",
        "status": "confirmed",
        "params": [
            {"name": "n", "type": "int", "description": "length"},
            {"name": "maxA", "type": "int", "description": "max element"},
        ],
        "input": None,
        "output": "the whole test",
        "algorithm": "random",
    },
    {
        "name": "gen_equal",
        "status": "proposed",
        "params": [{"name": "n", "type": "int", "description": "length"}],
        "input": None,
        "output": "the whole test",
        "algorithm": "all equal",
    },
]


def _design(ideas, constants=None):
    design = {"problem_id": "example-problem", "generators": GENERATORS, "ideas": ideas}
    if constants is not None:
        design["constants"] = constants
    return yaml.safe_dump(design, sort_keys=False)


def _idea(tests, groups="all", idea_id="specific_test_ideas[0]", **extra):
    idea = {"id": idea_id, "status": "confirmed", "what": "max tests", "tests": tests}
    if groups is not None:
        idea["groups"] = groups
    idea.update(extra)
    return idea


def _build(ideas, constraints=CONSTRAINTS, current_plan=None, constants=None):
    limits = parse_limits(constraints)
    design = parse_tests_design(_design(ideas, constants), limits)
    return build_tests_plan(design, limits, "example-problem", current_plan)


def _constants(plan):
    return {entry["name"]: entry["value"] for entry in plan["constants"]}


def test_formulas_become_named_values_of_each_group():
    plan = _build([_idea(["gen_rand -n={n.max} -maxA={a.max}"])])

    assert [(b["group"], b["commands"]) for b in plan["tests"]] == [
        ("1", ["gen_rand -n=n_max_g1 -maxA=a_max_g1"]),
        ("2", ["gen_rand -n=n_max_g2 -maxA=a_max"]),
        ("3", ["gen_rand -n=n_max -maxA=a_max"]),
    ]
    assert _constants(plan) == {
        "n_max_g1": "10",
        "a_max_g1": "1000",
        "n_max_g2": "1000",
        "a_max": "1000000000",
        "n_max": "100000",
    }
    assert plan["tests"][0]["covers"] == ["specific_test_ideas[0]"]


def test_lo_is_one_above_the_groups_a_group_depends_on():
    plan = _build([_idea(["gen_rand -n={n.lo} -maxA=5"])])

    assert [b["commands"][0] for b in plan["tests"]] == [
        "gen_rand -n=n_min -maxA=5",
        "gen_rand -n=n_lo_g2 -maxA=5",
        "gen_rand -n=n_lo_g3 -maxA=5",
    ]
    constants = _constants(plan)
    assert constants["n_lo_g2"] == "11"
    # Transitively: group 3 depends on 2, which depends on 1.
    assert constants["n_lo_g3"] == "1001"


def test_lo_without_an_own_range_is_an_error():
    # Group 3 depends on 2, whose `a` is already the full range.
    with pytest.raises(TestsDesignError, match=r"group 3.*a\.lo of group 3"):
        _build(
            [
                _idea(["gen_rand -n=1 -maxA={a.lo}"], groups=["2", "3"]),
                _idea(["gen_rand -n=1 -maxA=1"], groups=["1"], idea_id="own[0]"),
            ]
        )


def test_arithmetic_formula_gets_a_constant_over_the_bound():
    plan = _build(
        [
            _idea(["gen_rand -n={n.max / 2} -maxA={a.max - 1}"], groups=["3"]),
            _idea(["gen_rand -n=1 -maxA=1"], groups=["1", "2"], idea_id="own[0]"),
        ]
    )

    block = plan["tests"][-1]
    assert block["group"] == "3"
    assert block["commands"] == ["gen_rand -n=n_max_div_2 -maxA=a_max_minus_1"]
    constants = {entry["name"]: entry for entry in plan["constants"]}
    assert constants["n_max_div_2"]["value"] == "n_max / 2"
    assert "integer division" in constants["n_max_div_2"]["description"]
    assert constants["a_max_minus_1"]["value"] == "a_max - 1"


def test_one_idea_may_take_several_entries_on_disjoint_groups():
    plan = _build(
        [
            _idea(["gen_rand -n={n.max} -maxA={a.max}"], groups=["1", "2"]),
            _idea(["gen_equal -n={n.max}"], groups=["3"]),
        ]
    )
    assert [(b["group"], b["commands"][0].split()[0]) for b in plan["tests"]] == [
        ("1", "gen_rand"),
        ("2", "gen_rand"),
        ("3", "gen_equal"),
    ]


def test_blocks_go_in_group_order_then_idea_order():
    plan = _build(
        [
            _idea(["gen_equal -n=1"], idea_id="own[0]"),
            _idea(["gen_rand -n=2 -maxA=2"], groups=["2", "1"], idea_id="own[1]"),
        ]
    )
    assert [(b["group"], b["commands"][0]) for b in plan["tests"]] == [
        ("1", "gen_equal -n=1"),
        ("1", "gen_rand -n=2 -maxA=2"),
        ("2", "gen_equal -n=1"),
        ("2", "gen_rand -n=2 -maxA=2"),
        ("3", "gen_equal -n=1"),
    ]
    # Own ideas cover no spec item.
    assert plan["tests"][0]["covers"] == []


def test_without_groups_the_formulas_give_the_problem_limits():
    plan = _build([_idea(["gen_equal -n={n.max}"], groups=None)], constraints=FLAT_CONSTRAINTS)

    assert plan["tests"] == [
        {
            "description": "max tests",
            "status": "confirmed",
            "covers": ["specific_test_ideas[0]"],
            "commands": ["gen_equal -n=n_max"],
            "repeat": None,
        }
    ]


def test_repeat_and_own_constants_are_kept():
    plan = _build(
        [_idea(["gen_equal -n=k"], groups=None, repeat="k = {n.min}..few")],
        constraints=FLAT_CONSTRAINTS,
        constants=[{"name": "few", "value": "5", "description": "a few"}],
    )
    assert plan["tests"][0]["repeat"] == "k = n_min..few"
    assert [entry["name"] for entry in plan["constants"]] == ["few", "n_min"]


def test_a_group_without_tests_is_an_error():
    with pytest.raises(TestsDesignError, match=r"\['3'\] get no tests"):
        _build([_idea(["gen_equal -n=1"], groups=["1", "2"])])


@pytest.mark.parametrize(
    "ideas, message",
    [
        ([_idea(["gen_other -n=1"])], "generator 'gen_other' is not in 'generators'"),
        ([_idea(["gen_equal -m=1"])], "'gen_equal' has no parameter 'm'"),
        ([_idea(["gen_equal -n={m.max}"])], "no variable 'm'"),
        ([_idea(["gen_equal -n={n.max + x}"])], "must be integer arithmetic"),
        ([_idea(["gen_equal -n={n.top}"])], "must be integer arithmetic"),
        ([_idea(["gen_equal -n={n.max"])], "unbalanced braces"),
        ([_idea(["gen_equal -n=1"], groups=["0"])], "group 0 is the statement examples"),
        ([_idea(["gen_equal -n=1"], groups=["7"])], r"no such group\(s\)"),
        ([_idea(["gen_equal -n=1"], groups=None)], "must be 'all' or a non-empty list"),
        ([_idea(["gen_equal -n=1"], idea_id="idea[0]")], "specific_test_ideas"),
        ([_idea([])], "tests must be a non-empty list"),
        (
            [_idea(["gen_equal -n=1"], groups=["1", "2"]), _idea(["gen_equal -n=2"], groups=["2"])],
            r"in group\(s\) \['2'\] twice",
        ),
        ([], "'ideas' must be a non-empty list"),
    ],
)
def test_parse_tests_design_rejects(ideas, message):
    with pytest.raises(TestsDesignError, match=message):
        parse_tests_design(_design(ideas), parse_limits(CONSTRAINTS))


def test_groups_without_test_groups_are_rejected():
    with pytest.raises(TestsDesignError, match="has no test_groups"):
        parse_tests_design(_design([_idea(["gen_equal -n=1"])]), parse_limits(FLAT_CONSTRAINTS))


def test_wrong_solution_blocks_of_the_current_plan_are_kept():
    current = {
        "generators": [
            *GENERATORS,
            {"name": "gen_trap", "status": "proposed", "params": [], "algorithm": "x"},
            {"name": "gen_unused", "status": "proposed", "params": []},
        ],
        "constants": [
            {"name": "trapN", "value": "half + 1", "description": "x"},
            {"name": "half", "value": "50", "description": "x"},
            {"name": "other", "value": "1", "description": "x"},
        ],
        "tests": [
            {"description": "old base", "group": "3", "covers": [], "commands": ["gen_equal -n=1"]},
            {
                "description": "trap",
                "group": "2",
                "status": "proposed",
                "covers": ["solutions/wa_cpp_off.cpp"],
                "commands": ["gen_trap -n=trapN"],
            },
        ],
    }
    plan = _build([_idea(["gen_equal -n={n.max}"])], current_plan=current)

    assert [(b["group"], b["description"]) for b in plan["tests"]] == [
        ("1", "max tests"),
        ("2", "max tests"),
        ("2", "trap"),
        ("3", "max tests"),
    ]
    assert [g["name"] for g in plan["generators"]] == ["gen_rand", "gen_equal", "gen_trap"]
    names = [entry["name"] for entry in plan["constants"]]
    assert "trapN" in names and "half" in names and "other" not in names


def test_plan_from_design_gives_a_valid_plan_with_a_header():
    text = plan_from_design(
        _design(
            [
                _idea(["gen_rand -n={n.max} -maxA={a.lo}"], groups=["1", "2"]),
                _idea(["gen_equal -n={n.max}"], groups=["3"], idea_id="own[0]"),
            ]
        ),
        CONSTRAINTS,
        "example-problem",
    )
    assert text.startswith(PLAN_HEADER)
    plan = parse_tests_plan(text)
    assert plan["problem_id"] == "example-problem"
    assert len(plan["tests"]) == 3
