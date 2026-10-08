from pathlib import Path

import pytest

from orchestrator import cli
from orchestrator.polygon import pull as pull_module
from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.pull import (
    ITEM_ABSENT,
    ITEM_DOWNLOADED,
    ITEM_ERROR,
    ITEM_KEPT,
    pull_problem,
)
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps.base import PolygonStepError

PROBLEM_ID = "example-problem"
POLYGON_ID = 4242


class FakePolygon:
    """Задача на Polygon в памяти: отвечает на те методы, которые вызывает `pull`."""

    def __init__(self) -> None:
        self.problems = [{"id": POLYGON_ID, "name": PROBLEM_ID, "owner": "author"}]
        self.statements = {
            "russian": {"legend": "L", "input": "I", "output": "O", "notes": ""}
        }
        self.info = {"timeLimit": 2000, "memoryLimit": 256}
        self.validator = "val.cpp"
        self.checker = "std::ncmp.cpp"
        self.source_files = {"val.cpp": b"// validator\n", "gen_rand.cpp": b"// gen\n"}
        self.script = b"gen_rand 1 > $\n"
        self.solutions = {"ok_cpp_main_author.cpp": ("MA", b"// ok\n")}
        self.tests = [
            {"index": 1, "manual": True, "input": "1 2\n", "useInStatements": True},
            {"index": 2, "manual": False, "useInStatements": True},
            {"index": 3, "manual": False, "useInStatements": False},
        ]
        self.test_inputs = {2: b"3 4\n"}
        self.failing: set[str] = set()
        self.calls: list[tuple[str, dict]] = []

    def call(self, method_name, params):
        self.calls.append((method_name, params))
        if method_name in self.failing:
            raise PolygonApiError(method_name, "boom")
        if method_name == "problems.list":
            # Как и реальный фильтр `name` (не проверен) — возможно, неточный.
            return [
                p
                for p in self.problems
                if params["name"] in p["name"] and params.get("id") in (None, str(p["id"]))
            ]
        assert params["problemId"] == str(POLYGON_ID)
        return {
            "problem.statements": lambda: self.statements,
            "problem.info": lambda: self.info,
            "problem.validator": lambda: self.validator,
            "problem.checker": lambda: self.checker,
            "problem.files": lambda: {
                "resourceFiles": [{"name": "problem_lib.h"}],
                "sourceFiles": [{"name": name} for name in self.source_files],
                "auxFiles": [],
            },
            "problem.solutions": lambda: [
                {"name": name, "tag": tag} for name, (tag, _) in self.solutions.items()
            ],
            "problem.tests": lambda: self.tests,
        }[method_name]()

    def call_raw(self, method_name, params):
        self.calls.append((method_name, params))
        if method_name in self.failing:
            raise PolygonApiError(method_name, "boom")
        assert params["problemId"] == str(POLYGON_ID)
        if method_name == "problem.viewFile":
            assert params["type"] == "source"
            return self.source_files[params["name"]]
        if method_name == "problem.viewSolution":
            return self.solutions[params["name"]][1]
        if method_name == "problem.script":
            return self.script
        if method_name == "problem.testInput":
            return self.test_inputs[int(params["testIndex"])]
        raise AssertionError(method_name)

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def outputs_dir(tmp_path):
    return tmp_path / "outputs"


@pytest.fixture
def polygon():
    return FakePolygon()


def _statuses(result) -> dict[str, str]:
    return {item.name: item.status for item in result.items}


def _link(outputs_dir: Path, polygon_id: int = POLYGON_ID) -> None:
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=polygon_id,
            created_at="2026-09-19T12:00:00+00:00",
            steps={"upload_validator": {"sha256": "abc"}},
        ),
        outputs_dir=outputs_dir,
    )


def test_pull_into_missing_directory_downloads_everything(outputs_dir, polygon):
    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    problem_dir = outputs_dir / PROBLEM_ID
    assert result.ok
    assert result.polygon_id == POLYGON_ID
    assert load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).polygon_id == POLYGON_ID
    assert (problem_dir / "statement" / "legend.tex").read_text() == "L"
    assert (problem_dir / "statement" / "input_format.tex").read_text() == "I"
    assert (problem_dir / "statement" / "output_format.tex").read_text() == "O"
    # Пустой раздел выгружается пустым файлом — upload_statement требует все 4.
    assert (problem_dir / "statement" / "notes.tex").read_text() == ""
    assert (problem_dir / "statement" / "examples" / "example_1.txt").read_text() == "1 2\n"
    # Сгенерированный тест: input берётся через problem.testInput.
    assert (problem_dir / "statement" / "examples" / "example_2.txt").read_bytes() == b"3 4\n"
    assert not (problem_dir / "statement" / "examples" / "example_3.txt").exists()
    assert (problem_dir / "validator.cpp").read_bytes() == b"// validator\n"
    assert not (problem_dir / "checker.cpp").exists()
    assert [p.name for p in (problem_dir / "generators").iterdir()] == ["gen_rand.cpp"]
    assert (problem_dir / "test_script").read_bytes() == b"gen_rand 1 > $\n"
    assert (problem_dir / "solutions" / "ok_cpp_main_author.cpp").read_bytes() == b"// ok\n"
    assert _statuses(result)["checker.cpp"] == ITEM_ABSENT
    assert result.warnings == []


def test_pulled_limits_are_readable_by_set_constraints(outputs_dir, polygon):
    from orchestrator.polygon.steps.set_constraints import _build_update_params

    pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    params = _build_update_params(POLYGON_ID, PROBLEM_ID, outputs_dir)
    assert params["timeLimit"] == "2000"
    assert params["memoryLimit"] == "256"


def test_fractional_time_limit(outputs_dir, polygon):
    polygon.info = {"timeLimit": 1500, "memoryLimit": 512}

    pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    content = (outputs_dir / PROBLEM_ID / "constraints.yaml").read_text()
    assert "time_limit_seconds: 1.5\n" in content


def test_existing_files_are_kept_and_only_missing_are_downloaded(outputs_dir, polygon):
    problem_dir = outputs_dir / PROBLEM_ID
    (problem_dir / "statement" / "examples").mkdir(parents=True)
    (problem_dir / "statement" / "legend.tex").write_text("local legend")
    (problem_dir / "statement" / "examples" / "example_1.txt").write_text("local\n")
    (problem_dir / "validator.cpp").write_text("local validator")
    (problem_dir / "constraints.yaml").write_text("local: true\n")
    (problem_dir / "test_script_groups").write_text("local script")
    (problem_dir / "solutions").mkdir()
    (problem_dir / "solutions" / "ok_cpp_main_author.cpp").write_text("local solution")

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    statuses = _statuses(result)
    assert (problem_dir / "statement" / "legend.tex").read_text() == "local legend"
    assert (problem_dir / "statement" / "input_format.tex").read_text() == "I"
    assert statuses["statement/legend.tex"] == ITEM_KEPT
    assert statuses["statement/input_format.tex"] == ITEM_DOWNLOADED
    assert (problem_dir / "statement" / "examples" / "example_1.txt").read_text() == "local\n"
    assert statuses["statement/examples/example_2.txt"] == ITEM_DOWNLOADED
    assert (problem_dir / "validator.cpp").read_text() == "local validator"
    assert (problem_dir / "constraints.yaml").read_text() == "local: true\n"
    assert statuses["test_script_groups"] == ITEM_KEPT
    assert not (problem_dir / "test_script").exists()
    assert (problem_dir / "solutions" / "ok_cpp_main_author.cpp").read_text() == "local solution"
    # Ничего не скачивается ради того, что и так есть локально.
    assert "problem.info" not in polygon.methods()
    assert "problem.script" not in polygon.methods()
    assert "problem.viewSolution" not in polygon.methods()


def test_existing_link_is_reused_without_search_and_steps_are_preserved(outputs_dir, polygon):
    _link(outputs_dir)

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    assert "problems.list" not in polygon.methods()
    assert result.link_detail == f"already linked to Polygon id={POLYGON_ID}"
    state = load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir)
    assert state.created_at == "2026-09-19T12:00:00+00:00"
    assert state.steps == {"upload_validator": {"sha256": "abc"}}


def test_conflicting_polygon_id_is_an_error(outputs_dir, polygon):
    _link(outputs_dir)

    with pytest.raises(PolygonStepError, match="already linked"):
        pull_problem(PROBLEM_ID, polygon_id=1, outputs_dir=outputs_dir, client=polygon)

    assert polygon.calls == []


def test_name_search_ignores_inexact_and_deleted_matches(outputs_dir, polygon):
    polygon.problems = [
        {"id": 1, "name": PROBLEM_ID + "-old"},
        {"id": 2, "name": PROBLEM_ID, "deleted": True},
        {"id": POLYGON_ID, "name": PROBLEM_ID},
    ]

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    assert result.polygon_id == POLYGON_ID


def test_unknown_name_is_an_error_and_writes_nothing(outputs_dir, polygon):
    polygon.problems = []

    with pytest.raises(PolygonStepError, match="no Polygon problem named"):
        pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    assert not (outputs_dir / PROBLEM_ID).exists()


def test_ambiguous_name_is_an_error(outputs_dir, polygon):
    polygon.problems = [
        {"id": 1, "name": PROBLEM_ID, "owner": "a"},
        {"id": 2, "name": PROBLEM_ID, "owner": "b"},
    ]

    with pytest.raises(PolygonStepError, match="several Polygon problems"):
        pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)


def test_search_always_sends_name_and_adds_id_only_when_given(outputs_dir, polygon):
    pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)
    assert polygon.calls[0] == ("problems.list", {"name": PROBLEM_ID, "showDeleted": "false"})

    (outputs_dir / PROBLEM_ID / "polygon_state.json").unlink()
    polygon.calls.clear()
    pull_problem(PROBLEM_ID, polygon_id=POLYGON_ID, outputs_dir=outputs_dir, client=polygon)
    assert polygon.calls[0] == (
        "problems.list",
        {"name": PROBLEM_ID, "showDeleted": "false", "id": str(POLYGON_ID)},
    )


def test_explicit_polygon_id_picks_one_of_same_named_problems(outputs_dir, polygon):
    polygon.problems = [
        {"id": 1, "name": PROBLEM_ID, "owner": "a"},
        {"id": POLYGON_ID, "name": PROBLEM_ID, "owner": "b"},
    ]

    result = pull_problem(
        PROBLEM_ID, polygon_id=POLYGON_ID, outputs_dir=outputs_dir, client=polygon
    )

    assert result.polygon_id == POLYGON_ID
    assert load_polygon_state(PROBLEM_ID, outputs_dir=outputs_dir).polygon_id == POLYGON_ID


@pytest.mark.parametrize(
    "problems",
    [
        [{"id": POLYGON_ID, "name": "another-name"}],
        [{"id": POLYGON_ID, "name": PROBLEM_ID, "deleted": True}],
        [{"id": 7, "name": PROBLEM_ID}],
    ],
    ids=["name mismatch", "deleted", "other id"],
)
def test_explicit_polygon_id_still_requires_name_and_not_deleted(outputs_dir, polygon, problems):
    polygon.problems = problems

    with pytest.raises(PolygonStepError, match=f"with id={POLYGON_ID}"):
        pull_problem(PROBLEM_ID, polygon_id=POLYGON_ID, outputs_dir=outputs_dir, client=polygon)

    assert not (outputs_dir / PROBLEM_ID).exists()


def test_custom_checker_is_downloaded_and_not_treated_as_generator(outputs_dir, polygon):
    polygon.checker = "check.cpp"
    polygon.source_files["check.cpp"] = b"// checker\n"

    pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    problem_dir = outputs_dir / PROBLEM_ID
    assert (problem_dir / "checker.cpp").read_bytes() == b"// checker\n"
    assert [p.name for p in (problem_dir / "generators").iterdir()] == ["gen_rand.cpp"]


def test_failed_part_does_not_stop_the_others(outputs_dir, polygon):
    polygon.failing = {"problem.statements", "problem.viewSolution"}

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    statuses = _statuses(result)
    assert not result.ok
    assert statuses["statement"] == ITEM_ERROR
    assert statuses["solutions/ok_cpp_main_author.cpp"] == ITEM_ERROR
    assert statuses["validator.cpp"] == ITEM_DOWNLOADED
    assert not (outputs_dir / PROBLEM_ID / "solutions" / "ok_cpp_main_author.cpp").exists()


def test_missing_parts_on_polygon_are_reported_as_absent(outputs_dir, polygon):
    polygon.statements = {}
    polygon.validator = ""
    polygon.source_files = {}
    polygon.script = b""
    polygon.solutions = {}
    polygon.tests = []

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    statuses = _statuses(result)
    assert result.ok
    for name in (
        "statement/legend.tex",
        "statement/examples",
        "validator.cpp",
        "generators",
        "test_script",
        "solutions",
    ):
        assert statuses[name] == ITEM_ABSENT, name
    assert not (outputs_dir / PROBLEM_ID / "statement").exists()


def test_solution_name_outside_naming_scheme_gives_warning(outputs_dir, polygon):
    polygon.solutions = {"sol.cpp": ("MA", b"// sol\n")}

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    assert (outputs_dir / PROBLEM_ID / "solutions" / "sol.cpp").exists()
    assert len(result.warnings) == 1 and "sol.cpp" in result.warnings[0]


def test_unsafe_file_name_from_polygon_is_not_written(outputs_dir, polygon):
    polygon.solutions = {"../escape.cpp": ("OK", b"x")}

    result = pull_problem(PROBLEM_ID, outputs_dir=outputs_dir, client=polygon)

    assert _statuses(result)["solutions/../escape.cpp"] == ITEM_ERROR
    assert not (outputs_dir / PROBLEM_ID / "escape.cpp").exists()


def test_cli_pull_needs_no_spec_and_logs_to_polygon_log(
    tmp_path, outputs_dir, polygon, capsys, monkeypatch
):
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    exit_code = cli.main(
        [
            "--specs-dir",
            str(tmp_path / "no-specs"),
            "--outputs-dir",
            str(outputs_dir),
            "polygon",
            PROBLEM_ID,
            "pull",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "  validator.cpp: downloaded — Polygon source file val.cpp" in out
    assert f"found Polygon problem named {PROBLEM_ID!r} (id={POLYGON_ID}), linked" in out
    log = (outputs_dir / PROBLEM_ID / "polygon.log").read_text(encoding="utf-8")
    assert "run started: orchestrator --specs-dir" in log
    assert "[pull] validator.cpp: downloaded" in log


def test_cli_pull_unresolved_problem_exits_with_error(outputs_dir, polygon, capsys, monkeypatch):
    polygon.problems = []
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    exit_code = cli.main(["--outputs-dir", str(outputs_dir), "polygon", PROBLEM_ID, "pull"])

    assert exit_code == 1
    assert "no Polygon problem named" in capsys.readouterr().err
