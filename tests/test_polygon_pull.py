from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from polygon_sync_helpers import FIXTURES_DIR, POLYGON_ID, PROBLEM_ID, link

from orchestrator import cli
from orchestrator.polygon import pull as pull_module
from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.pull import (
    ITEM_ABSENT,
    ITEM_CONFLICT,
    ITEM_DIFFERS,
    ITEM_DOWNLOADED,
    ITEM_ERROR,
    ITEM_KEPT,
    ITEM_LOCAL_ONLY,
    ITEM_OVERWRITTEN,
    ITEM_SAME,
    ITEM_UPDATED,
    pull_problem,
)
from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
)
from orchestrator.polygon.steps import (
    set_constraints,
    upload_attachments,
    upload_generators,
    upload_interactor,
    upload_resources,
    upload_solutions,
    upload_statement,
    upload_test_groups,
    upload_test_script,
    upload_validator,
)
from orchestrator.polygon.steps.base import PolygonStepError, run_step
from orchestrator.polygon.steps.upload_test_script import normalize_script
from orchestrator.polygon.sync import PULLED_CONSTRAINTS_HEADER
from orchestrator.resources import STATEMENT_RESOURCES_DIR, TESTLIB_DIR, resource_path
from orchestrator.spec import load_spec
from orchestrator.steps.base import template_problem_lib_path


class FakePolygon:
    """A problem on Polygon in memory: answers the methods `pull` calls."""

    def __init__(self) -> None:
        self.problems = [
            {
                "id": POLYGON_ID,
                "name": PROBLEM_ID,
                "owner": "author",
                "revision": 5,
                "modified": False,
            }
        ]
        self.statements = {
            "russian": {
                "name": "Polygon Name",
                "legend": "L",
                "input": "I",
                "output": "O",
                "notes": "",
            }
        }
        self.info = {"timeLimit": 2000, "memoryLimit": 256}
        self.validator = "val.cpp"
        self.checker = "std::ncmp.cpp"
        self.interactor: str | None = None
        self.source_files = {"val.cpp": b"// validator\n", "gen_rand.cpp": b"// gen\n"}
        self.resource_files = {"problem_lib.h": template_problem_lib_path().read_bytes()}
        self.aux_files = {"spec.yaml": b"problem_id: example-problem\n"}
        self.script = b"gen_rand 1 > $\n"
        self.solutions = {"ma_cpp_sum_author.cpp": ("MA", b"// ok\n")}
        self.tests = [
            {"index": 1, "manual": True, "input": "1 2\n", "useInStatements": True},
            {"index": 2, "manual": False, "useInStatements": True},
            {"index": 3, "manual": False, "useInStatements": False},
        ]
        self.test_inputs = {2: b"3 4\n"}
        # TestGroup objects; `None` — groups aren't enabled (viewTestGroup fails), a string
        # — viewTestGroup fails with this comment.
        self.test_groups: list[dict] | None = None
        # The modification time of files and solutions in the listings (100 by default);
        # `None` — Polygon doesn't return it.
        self.mtimes: dict[str, int | None] = {}
        self.failing: set[str] = set()
        self.calls: list[tuple[str, dict]] = []

    def call(self, method_name, params):
        self.calls.append((method_name, params))
        if method_name in self.failing:
            raise PolygonApiError(method_name, "boom")
        if method_name == "problems.list":
            # Like the real `name` filter (not verified) — possibly inexact.
            return [
                p
                for p in self.problems
                if params.get("name", "") in p["name"] and params.get("id") in (None, str(p["id"]))
            ]
        assert params["problemId"] == str(POLYGON_ID)
        return {
            "problem.statements": lambda: self.statements,
            "problem.info": lambda: self.info,
            "problem.validator": lambda: self.validator,
            "problem.checker": lambda: self.checker,
            "problem.interactor": lambda: self.interactor,
            "problem.files": lambda: {
                "resourceFiles": [self._listed(n, c) for n, c in self.resource_files.items()],
                "sourceFiles": [self._listed(n, c) for n, c in self.source_files.items()],
                "auxFiles": [self._listed(n, c) for n, c in self.aux_files.items()],
            },
            "problem.solutions": lambda: [
                {**self._listed(name, content), "tag": tag}
                for name, (tag, content) in self.solutions.items()
            ],
            "problem.tests": lambda: self.tests,
            "problem.viewTestGroup": self._test_groups,
        }[method_name]()

    def _test_groups(self) -> list[dict]:
        if self.test_groups is None:
            raise PolygonApiError(
                "problem.viewTestGroup",
                "testset: Test groups are disabled for the specified testset",
            )
        if isinstance(self.test_groups, str):
            raise PolygonApiError("problem.viewTestGroup", self.test_groups)
        return self.test_groups

    def call_raw(self, method_name, params):
        self.calls.append((method_name, params))
        if method_name in self.failing:
            raise PolygonApiError(method_name, "boom")
        assert params["problemId"] == str(POLYGON_ID)
        if method_name == "problem.viewFile":
            files = {
                "source": self.source_files,
                "resource": self.resource_files,
                "aux": self.aux_files,
            }[params["type"]]
            return files[params["name"]]
        if method_name == "problem.viewSolution":
            return self.solutions[params["name"]][1]
        if method_name == "problem.script":
            return self.script
        if method_name == "problem.testInput":
            return self.test_inputs[int(params["testIndex"])]
        raise AssertionError(method_name)

    def _listed(self, name: str, content: bytes) -> dict:
        listed = {"name": name, "length": len(content)}
        mtime = self.mtimes.get(name, 100)
        if mtime is not None:
            listed["modificationTimeSeconds"] = mtime
        return listed

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]

    def downloads(self) -> list[str]:
        """The downloaded files with stamps: source/resource/aux files and solutions."""
        return [
            params["name"]
            for name, params in self.calls
            if name in ("problem.viewFile", "problem.viewSolution")
        ]


@pytest.fixture
def polygon():
    return FakePolygon()


def _statuses(result) -> dict[str, str]:
    return {item.name: item.status for item in result.items}


def _link(problems_dir: Path, polygon_id: int = POLYGON_ID, *, revision: int | None = None) -> None:
    link(problems_dir, polygon_id=polygon_id, revision=revision, synced={"stale/key": "abc"})


def _synced(problems_dir: Path) -> dict[str, str]:
    return load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).synced


def _write(problem_dir: Path, name: str, content: str) -> Path:
    path = problem_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_pull_into_missing_directory_downloads_everything(problems_dir, polygon):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    problem_dir = problems_dir / PROBLEM_ID
    assert result.ok
    assert result.polygon_id == POLYGON_ID
    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).polygon_id == POLYGON_ID
    assert (problem_dir / "statement" / "ru" / "legend.tex").read_text() == "L"
    assert (problem_dir / "statement" / "ru" / "input_format.tex").read_text() == "I"
    assert (problem_dir / "statement" / "ru" / "output_format.tex").read_text() == "O"
    # An empty section is downloaded as an empty file — upload_statement requires all 4.
    assert (problem_dir / "statement" / "ru" / "notes.tex").read_text() == ""
    assert (problem_dir / "statement" / "ru" / "name.txt").read_text() == "Polygon Name\n"
    assert (problem_dir / "statement" / "examples" / "example_1.txt").read_text() == "1 2\n"
    # A generated test: the input is taken via problem.testInput.
    assert (problem_dir / "statement" / "examples" / "example_2.txt").read_bytes() == b"3 4\n"
    assert not (problem_dir / "statement" / "examples" / "example_3.txt").exists()
    assert (problem_dir / "validator.cpp").read_bytes() == b"// validator\n"
    assert not (problem_dir / "checker.cpp").exists()
    assert [p.name for p in (problem_dir / "generators").iterdir()] == ["gen_rand.cpp"]
    assert (problem_dir / "test_script").read_bytes() == b"gen_rand 1 > $\n"
    assert (problem_dir / "solutions" / "ma_cpp_sum_author.cpp").read_bytes() == b"// ok\n"
    assert _statuses(result)["checker.cpp"] == ITEM_ABSENT
    assert (problem_dir / "resources" / "problem_lib.h").read_bytes() == polygon.resource_files[
        "problem_lib.h"
    ]
    assert (problem_dir / "authoring" / "spec.yaml").read_bytes() == polygon.aux_files["spec.yaml"]
    assert result.warnings == []
    assert result.backup_dir is None


def test_pull_records_revision_and_baseline(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    assert state.revision == 5
    assert sorted(state.synced) == [
        "checker",
        "generators/gen_rand.cpp",
        "limits",
        "problem_lib.h",
        "solutions/ma_cpp_sum_author.cpp",
        "spec.yaml",
        "statement/examples/example_1.txt",
        "statement/examples/example_2.txt",
        "statement/ru/input_format.tex",
        "statement/ru/legend.tex",
        "statement/ru/name",
        "statement/ru/notes.tex",
        "statement/ru/output_format.tex",
        "test_script",
        "validator",
        "validator.cpp",
    ]


def _flat_spec_text() -> str:
    """The fixture spec with a flat script and so without test groups."""
    hint = '  test_groups_hint:\n    - name: "1"\n      variables: {N: {max: 10}}\n'
    text = (FIXTURES_DIR / "valid-spec.yaml").read_text(encoding="utf-8")
    assert hint in text
    return text.replace('script_style: "groups"', 'script_style: "flat"').replace(hint, "")


def test_push_after_pull_uploads_only_what_differs_from_polygon(tmp_path, problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    # upload_test_script rejects groups — flat is needed
    spec_path = tmp_path / "valid-spec.yaml"
    spec_path.write_text(
        _flat_spec_text(),
        encoding="utf-8",
    )
    spec = load_spec(spec_path)
    synced = _synced(problems_dir)

    def uploaded(step) -> dict:
        return run_step(
            step, PROBLEM_ID, spec, problems_dir=problems_dir, client=MagicMock(), synced=synced
        ).uploaded

    for step in (
        set_constraints.STEP,
        upload_resources.STEP,
        upload_generators.STEP,
        upload_test_script.STEP,
        upload_solutions.STEP,
        upload_attachments.STEP,
    ):
        assert uploaded(step) == {}, step.name
    # on Polygon the validator is assigned under another name — push uploads validator.cpp
    assert set(uploaded(upload_validator.STEP)) == {"validator", "validator.cpp"}


def test_name_is_compared_without_surrounding_whitespace(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/ru/name.txt", "  Polygon Name")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["statement/ru/name.txt"] == ITEM_SAME
    assert _synced(problems_dir)["statement/ru/name"] == pull_module.digest("Polygon Name")


def test_name_renamed_on_polygon_is_not_pushed_back_after_pull(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/ru/name.txt", "Old Name\n")

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)
    assert _statuses(result)["statement/ru/name.txt"] == ITEM_OVERWRITTEN

    # the spec's title.ru is no longer the source of the name
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    uploaded = run_step(
        upload_statement.STEP,
        PROBLEM_ID,
        spec,
        problems_dir=problems_dir,
        client=MagicMock(),
        synced=_synced(problems_dir),
    ).uploaded
    assert "statement/ru/name" not in uploaded


def test_script_without_blank_lines_on_polygon_is_same_and_not_pushed_again(
    tmp_path, problems_dir, polygon
):
    # Polygon drops empty lines from a saved script.
    problem_dir = problems_dir / PROBLEM_ID
    local = "gen_rand 1 > $\n\n\ngen_rand 2 > $\n"
    _write(problem_dir, "test_script", local)
    polygon.script = b"gen_rand 1 > $\ngen_rand 2 > $\n"

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["test_script"] == ITEM_SAME
    assert (problem_dir / "test_script").read_text() == local
    spec_path = tmp_path / "valid-spec.yaml"
    spec_path.write_text(
        _flat_spec_text(),
        encoding="utf-8",
    )
    uploaded = run_step(
        upload_test_script.STEP,
        PROBLEM_ID,
        load_spec(spec_path),
        problems_dir=problems_dir,
        client=MagicMock(),
        synced=_synced(problems_dir),
    ).uploaded
    assert uploaded == {}


def test_uncommitted_changes_on_polygon_stop_pull_before_writing(problems_dir, polygon):
    polygon.problems[0]["modified"] = True

    with pytest.raises(PolygonStepError, match="uncommitted changes"):
        pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not (problems_dir / PROBLEM_ID / "validator.cpp").exists()


def test_pulled_limits_are_readable_by_set_constraints(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (2000, 256)


def test_fractional_time_limit(problems_dir, polygon):
    polygon.info = {"timeLimit": 1500, "memoryLimit": 512}

    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    content = (problems_dir / PROBLEM_ID / "limits.yaml").read_text()
    assert "time_limit_seconds: 1.5\n" in content
    assert "memory_limit_mb: 512\n" in content


def test_differing_files_are_kept_with_polygon_version_in_baseline(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/ru/legend.tex", "local legend")
    _write(problem_dir, "statement/examples/example_1.txt", "1 2\n")
    _write(problem_dir, "validator.cpp", "local validator")
    _write(problem_dir, "test_script", "local script")
    _write(problem_dir, "solutions/ma_cpp_sum_author.cpp", "// ok\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    # There's no base — who changed it is unknown.
    assert statuses["statement/ru/legend.tex"] == ITEM_DIFFERS
    assert statuses["statement/ru/input_format.tex"] == ITEM_DOWNLOADED
    assert statuses["statement/examples/example_1.txt"] == ITEM_SAME
    assert statuses["validator.cpp"] == ITEM_DIFFERS
    assert statuses["test_script"] == ITEM_DIFFERS
    assert statuses["solutions/ma_cpp_sum_author.cpp"] == ITEM_SAME
    assert (problem_dir / "statement" / "ru" / "legend.tex").read_text() == "local legend"
    assert (problem_dir / "validator.cpp").read_text() == "local validator"
    assert (problem_dir / "test_script").read_text() == "local script"
    synced = _synced(problems_dir)
    # The base has the version from Polygon: push compares the local one with it and uploads.
    assert synced["statement/ru/legend.tex"] == pull_module.digest("L")
    assert synced["validator.cpp"] == pull_module.digest(b"// validator\n")
    assert synced["test_script"] == pull_module.digest(normalize_script(polygon.script))
    # The validator assignment isn't confirmed — push assigns it again.
    assert "validator" not in synced
    for key in ("statement/ru/input_format.tex", "statement/examples/example_1.txt"):
        assert key in synced, key
    assert result.backup_dir is None


def test_overwrite_replaces_differing_files_and_backs_them_up(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/ru/legend.tex", "local legend")
    _write(problem_dir, "validator.cpp", "local validator")
    _write(problem_dir, "generators/gen_rand.cpp", "// gen\n")

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert statuses["statement/ru/legend.tex"] == ITEM_OVERWRITTEN
    assert statuses["validator.cpp"] == ITEM_OVERWRITTEN
    assert statuses["generators/gen_rand.cpp"] == ITEM_SAME
    assert (problem_dir / "statement" / "ru" / "legend.tex").read_text() == "L"
    assert (problem_dir / "validator.cpp").read_bytes() == b"// validator\n"
    assert result.backup_dir.parent == problem_dir / ".pull_backup"
    assert (result.backup_dir / "statement" / "ru" / "legend.tex").read_text() == "local legend"
    assert (result.backup_dir / "validator.cpp").read_text() == "local validator"
    assert not (result.backup_dir / "generators").exists()
    assert {"statement/ru/legend.tex", "validator.cpp", "validator"} <= set(_synced(problems_dir))


def test_line_endings_do_not_make_files_differ(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "validator.cpp", "")
    (problems_dir / PROBLEM_ID / "validator.cpp").write_bytes(b"// validator\r\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["validator.cpp"] == ITEM_SAME
    assert "validator.cpp" in _synced(problems_dir)


def test_local_only_files_are_reported_and_kept(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "generators/gen_local.cpp", "// local\n")
    _write(problem_dir, "solutions/wa_cpp_local_author.cpp", "// wa\n")
    _write(problem_dir, "statement/examples/example_3.txt", "5\n")

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    for name in (
        "generators/gen_local.cpp",
        "solutions/wa_cpp_local_author.cpp",
        "statement/examples/example_3.txt",
    ):
        assert statuses[name] == ITEM_LOCAL_ONLY, name
        assert (problem_dir / name).exists()
        assert name not in _synced(problems_dir)


def test_differing_limits_without_base_are_kept_without_overwrite(problems_dir, polygon):
    _write(
        problems_dir / PROBLEM_ID, "limits.yaml", "time_limit_seconds: 1\nmemory_limit_mb: 256\n"
    )

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "limits.yaml")
    assert item.status == ITEM_DIFFERS
    assert "1000ms/256MB" in item.detail and "2000ms/256MB" in item.detail
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (1000, 256)
    assert "limits" not in _synced(problems_dir)


def test_overwrite_replaces_limits_and_backs_them_up(problems_dir, polygon):
    original = "# tight\ntime_limit_seconds: 1\nmemory_limit_mb: 256\n"
    _write(problems_dir / PROBLEM_ID, "limits.yaml", original)

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["limits.yaml"] == ITEM_OVERWRITTEN
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (2000, 256)
    assert "limits" in _synced(problems_dir)
    assert (result.backup_dir / "limits.yaml").read_text() == original


def test_limits_never_touch_constraints_yaml(problems_dir, polygon):
    original = "limits:\n  time_limit_seconds: 1\n  memory_limit_mb: 64\ngroups: []\n"
    path = _write(problems_dir / PROBLEM_ID, "authoring/constraints.yaml", original)

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert path.read_text() == original
    assert _statuses(result)["limits.yaml"] == ITEM_DOWNLOADED
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (2000, 256)


def test_limits_changed_only_on_polygon_are_updated_without_overwrite(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    polygon.info = {"timeLimit": 3000, "memoryLimit": 256}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["limits.yaml"] == ITEM_UPDATED
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (3000, 256)


def test_limits_changed_only_locally_are_kept_even_with_overwrite(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _write(
        problems_dir / PROBLEM_ID, "limits.yaml", "time_limit_seconds: 5\nmemory_limit_mb: 256\n"
    )

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["limits.yaml"] == ITEM_KEPT
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (5000, 256)
    assert _synced(problems_dir).get("limits") is None


def test_limits_changed_on_both_sides_are_a_conflict(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _write(
        problems_dir / PROBLEM_ID, "limits.yaml", "time_limit_seconds: 5\nmemory_limit_mb: 256\n"
    )
    polygon.info = {"timeLimit": 3000, "memoryLimit": 256}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    assert _statuses(result)["limits.yaml"] == ITEM_CONFLICT
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (5000, 256)

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)
    assert _statuses(result)["limits.yaml"] == ITEM_OVERWRITTEN
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (3000, 256)


def test_same_limits_are_up_to_date(problems_dir, polygon):
    _write(
        problems_dir / PROBLEM_ID, "limits.yaml", "time_limit_seconds: 2\nmemory_limit_mb: 256\n"
    )

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["limits.yaml"] == ITEM_SAME
    assert "limits" in _synced(problems_dir)


def test_problem_lib_from_polygon_is_downloaded_even_if_it_differs_from_template(
    problems_dir, polygon
):
    polygon.resource_files["problem_lib.h"] = b"// edited for this problem\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["resources/problem_lib.h"] == ITEM_DOWNLOADED
    assert (
        problems_dir / PROBLEM_ID / "resources" / "problem_lib.h"
    ).read_bytes() == b"// edited for this problem\n"
    assert _synced(problems_dir)["problem_lib.h"] == pull_module.digest(
        b"// edited for this problem\n"
    )


def test_different_local_problem_lib_is_kept(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "resources/problem_lib.h", "// local edits\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["resources/problem_lib.h"] == ITEM_DIFFERS
    assert (
        problems_dir / PROBLEM_ID / "resources" / "problem_lib.h"
    ).read_text() == "// local edits\n"
    assert _synced(problems_dir)["problem_lib.h"] == pull_module.digest(
        polygon.resource_files["problem_lib.h"]
    )


def test_problem_lib_missing_on_polygon(problems_dir, polygon):
    polygon.resource_files = {}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["resources/problem_lib.h"] == ITEM_ABSENT
    assert "problem_lib.h" not in _synced(problems_dir)


# --- Resources other than problem_lib.h: compared with what the problem uses ---

_TESTLIB = "resources/testlib.h"


def test_resource_same_as_default_is_not_written(problems_dir, polygon):
    polygon.resource_files["testlib.h"] = TESTLIB_DIR.joinpath("testlib.h").read_bytes()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[_TESTLIB] == ITEM_SAME
    assert not (problems_dir / PROBLEM_ID / _TESTLIB).exists()
    assert _synced(problems_dir)[_TESTLIB] == pull_module.digest(
        polygon.resource_files["testlib.h"]
    )


def test_resource_different_from_default_becomes_the_problems_own(tmp_path, problems_dir, polygon):
    polygon.resource_files["testlib.h"] = b"// newer testlib on Polygon\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[_TESTLIB] == ITEM_DOWNLOADED
    path = problems_dir / PROBLEM_ID / _TESTLIB
    assert path.read_bytes() == b"// newer testlib on Polygon\n"
    assert resource_path(problems_dir / PROBLEM_ID, "testlib.h") == path
    # push doesn't upload it back: it equals the base
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    assert (
        run_step(
            upload_resources.STEP,
            PROBLEM_ID,
            spec,
            problems_dir=problems_dir,
            client=MagicMock(),
            synced=_synced(problems_dir),
        ).uploaded
        == {}
    )


def test_own_resource_is_compared_three_way(problems_dir, polygon):
    polygon.resource_files["testlib.h"] = b"// polygon\n"
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _write(problems_dir / PROBLEM_ID, _TESTLIB, "// local edits\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[_TESTLIB] == ITEM_KEPT
    assert (problems_dir / PROBLEM_ID / _TESTLIB).read_text() == "// local edits\n"


def test_resource_without_a_local_version_is_skipped(problems_dir, polygon):
    polygon.resource_files["picture.mp"] = b"% a statement picture\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert "resources/picture.mp" not in _statuses(result)
    assert not (problems_dir / PROBLEM_ID / "resources" / "picture.mp").exists()
    assert "resources/picture.mp" not in _synced(problems_dir)
    assert "picture.mp" not in polygon.downloads()


@pytest.mark.parametrize("name", ["olymp.sty", "problem.tex", "statements.ftl"])
def test_statement_resource_same_as_default_is_not_written(problems_dir, polygon, name):
    # Polygon may send it with other line breaks than the default's
    default = (STATEMENT_RESOURCES_DIR / name).read_bytes()
    polygon.resource_files[name] = default.replace(b"\r\n", b"\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[f"resources/{name}"] == ITEM_SAME
    assert not (problems_dir / PROBLEM_ID / "resources" / name).exists()


def test_statement_resource_different_from_default_becomes_the_problems_own(problems_dir, polygon):
    polygon.resource_files["olymp.sty"] = b"% edited on Polygon\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["resources/olymp.sty"] == ITEM_DOWNLOADED
    path = problems_dir / PROBLEM_ID / "resources" / "olymp.sty"
    assert path.read_bytes() == b"% edited on Polygon\n"


def test_own_resource_missing_on_polygon_is_local_only(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, _TESTLIB, "// own testlib\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[_TESTLIB] == ITEM_LOCAL_ONLY
    assert _TESTLIB not in _synced(problems_dir)


def test_resource_same_as_default_is_not_downloaded_again(problems_dir, polygon):
    polygon.resource_files["testlib.h"] = TESTLIB_DIR.joinpath("testlib.h").read_bytes()
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    polygon.problems[0]["revision"] += 1
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[_TESTLIB] == ITEM_SAME
    assert "testlib.h" not in polygon.downloads()


def test_different_local_spec_is_kept(problems_dir, polygon):
    _write(
        problems_dir / PROBLEM_ID, "authoring/spec.yaml", "problem_id: example-problem\n# local\n"
    )

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["authoring/spec.yaml"] == ITEM_DIFFERS
    assert _synced(problems_dir)["spec.yaml"] == pull_module.digest(polygon.aux_files["spec.yaml"])


def test_spec_missing_on_polygon(problems_dir, polygon):
    polygon.aux_files = {}
    _write(problems_dir / PROBLEM_ID, "authoring/spec.yaml", "problem_id: example-problem\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["authoring/spec.yaml"] == ITEM_LOCAL_ONLY
    assert "spec.yaml" not in _synced(problems_dir)


_CONSTRAINTS_PICK = (
    "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n"
    "groups: []\nvalidator: {constants: [], checks: []}\n"
)


def test_attachments_are_downloaded(problems_dir, polygon):
    polygon.aux_files.update(
        {
            "constraints.yaml": _CONSTRAINTS_PICK.encode(),
            "tests_plan.yaml": b"generators: []\n",
            "tests_design.yaml": b"design\n",
            "scoring.yaml": b"subtasks: []\n",
            "draft.md": b"idea\n",
            "draft-tutorial.md": b"tutorial draft\n",
            "other.txt": b"x",
        }
    )

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    problem_dir = problems_dir / PROBLEM_ID
    assert result.ok
    assert (problem_dir / "authoring" / "draft-tutorial.md").read_text() == "tutorial draft\n"
    assert (problem_dir / "authoring" / "constraints.yaml").read_text() == _CONSTRAINTS_PICK
    assert (problem_dir / "authoring" / "draft.md").read_text() == "idea\n"
    assert (problem_dir / "authoring" / "tests_plan.yaml").read_text() == "generators: []\n"
    assert (problem_dir / "authoring" / "tests_design.yaml").read_text() == "design\n"
    assert (problem_dir / "authoring" / "scoring.yaml").read_text() == "subtasks: []\n"
    # Pull doesn't touch other aux files.
    assert not (problem_dir / "other.txt").exists()
    statuses = [(item.name, item.status) for item in result.items]
    assert ("authoring/constraints.yaml", ITEM_DOWNLOADED) in statuses
    assert ("authoring/draft.md", ITEM_DOWNLOADED) in statuses
    synced = _synced(problems_dir)
    assert synced["constraints.yaml"] == pull_module.digest(_CONSTRAINTS_PICK)
    assert synced["draft.md"] == pull_module.digest(b"idea\n")
    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    assert state.stamps["draft.md"] == "draft.md:100:5"


def test_attached_constraints_replace_legacy_pulled_limits_only_file(problems_dir, polygon):
    # Previous versions of pull wrote such a file.
    stub = PULLED_CONSTRAINTS_HEADER + "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n"
    path = _write(problems_dir / PROBLEM_ID, "authoring/constraints.yaml", stub)
    polygon.aux_files["constraints.yaml"] = _CONSTRAINTS_PICK.encode()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert path.read_text() == _CONSTRAINTS_PICK
    assert (result.backup_dir / "authoring" / "constraints.yaml").read_text() == stub


def test_legacy_pulled_limits_only_file_is_not_reported_for_push(problems_dir, polygon):
    stub = PULLED_CONSTRAINTS_HEADER + "limits:\n  time_limit_seconds: 2\n  memory_limit_mb: 256\n"
    _write(problems_dir / PROBLEM_ID, "authoring/constraints.yaml", stub)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert "authoring/constraints.yaml" not in _statuses(result)


def test_limits_changed_on_polygon_do_not_change_attached_constraints(problems_dir, polygon):
    # constraints.yaml in Attachments has the TL/ML chosen by constraints_pick,
    # while the TL was changed on Polygon: it's compared only with Attachments.
    polygon.aux_files["constraints.yaml"] = _CONSTRAINTS_PICK.encode()
    polygon.info = {"timeLimit": 3000, "memoryLimit": 256}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert (
        problems_dir / PROBLEM_ID / "authoring" / "constraints.yaml"
    ).read_text() == _CONSTRAINTS_PICK
    assert _statuses(result)["authoring/constraints.yaml"] == ITEM_DOWNLOADED
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (3000, 256)


def test_constraints_missing_in_attachments_is_left_for_push(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "authoring/constraints.yaml", _CONSTRAINTS_PICK)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["authoring/constraints.yaml"] == ITEM_LOCAL_ONLY
    assert "constraints.yaml" not in _synced(problems_dir)


def test_local_tests_plan_missing_on_polygon(problems_dir, polygon):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    assert "authoring/tests_plan.yaml" not in _statuses(result)

    _write(problems_dir / PROBLEM_ID, "authoring/tests_plan.yaml", "generators: []\n")
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["authoring/tests_plan.yaml"] == ITEM_LOCAL_ONLY
    assert "tests_plan.yaml" not in _synced(problems_dir)


@pytest.mark.parametrize("name", ["tests_design.yaml", "scoring.yaml"])
def test_local_authoring_file_missing_on_polygon(problems_dir, polygon, name):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    assert f"authoring/{name}" not in _statuses(result)

    _write(problems_dir / PROBLEM_ID, f"authoring/{name}", "x: 1\n")
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)[f"authoring/{name}"] == ITEM_LOCAL_ONLY
    assert name not in _synced(problems_dir)


def test_local_tutorial_draft_missing_on_polygon(problems_dir, polygon):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    assert "authoring/draft-tutorial.md" not in _statuses(result)

    _write(problems_dir / PROBLEM_ID, "authoring/draft-tutorial.md", "tutorial draft\n")
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["authoring/draft-tutorial.md"] == ITEM_LOCAL_ONLY
    assert "draft-tutorial.md" not in _synced(problems_dir)


def test_local_draft_missing_on_polygon(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "authoring/draft.txt", "idea\n")
    _write(problems_dir / PROBLEM_ID, "authoring/draft.md.removed", "old\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["authoring/draft.txt"] == ITEM_LOCAL_ONLY
    assert "authoring/draft.md.removed" not in _statuses(result)
    assert "draft.txt" not in _synced(problems_dir)


def test_local_checker_with_standard_checker_on_polygon(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "checker.cpp", "// local checker\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "checker.cpp")
    assert item.status == ITEM_LOCAL_ONLY
    assert "std::ncmp.cpp" in item.detail
    assert item.push_condition == "checker.custom_needed: true"
    synced = _synced(problems_dir)
    assert "checker" in synced and "checker.cpp" not in synced


def test_existing_link_is_reused_without_search_and_baseline_is_replaced(problems_dir, polygon):
    _link(problems_dir)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.calls[0] == ("problems.list", {"id": str(POLYGON_ID)})
    assert [method for method in polygon.methods() if method == "problems.list"] == [
        "problems.list"
    ]
    assert result.link_detail == f"already linked to Polygon id={POLYGON_ID}"
    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    assert state.created_at == "2026-09-19T12:00:00+00:00"
    assert state.revision == 5
    # pull doesn't touch other keys — it rewrites only what it compared
    assert state.synced["stale/key"] == "abc"


def test_conflicting_polygon_id_is_an_error(problems_dir, polygon):
    _link(problems_dir)

    with pytest.raises(PolygonStepError, match="already linked"):
        pull_problem(PROBLEM_ID, polygon_id=1, problems_dir=problems_dir, client=polygon)

    assert polygon.calls == []


def test_name_search_ignores_inexact_and_deleted_matches(problems_dir, polygon):
    polygon.problems = [
        {"id": 1, "name": PROBLEM_ID + "-old"},
        {"id": 2, "name": PROBLEM_ID, "deleted": True},
        {"id": POLYGON_ID, "name": PROBLEM_ID},
    ]

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert result.polygon_id == POLYGON_ID


def test_unknown_name_is_an_error_and_writes_nothing(problems_dir, polygon):
    polygon.problems = []

    with pytest.raises(PolygonStepError, match="no Polygon problem named"):
        pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not (problems_dir / PROBLEM_ID).exists()


def test_nested_example_is_searched_by_joined_problem_id(tmp_path, polygon):
    examples = tmp_path / "examples"
    polygon.problems[0]["name"] = "array--prefix-sums"

    result = pull_problem("array/prefix-sums", problems_dir=examples, client=polygon)

    assert result.polygon_id == POLYGON_ID
    assert (
        "problems.list",
        {"name": "array--prefix-sums", "showDeleted": "false"},
    ) in polygon.calls
    state = load_polygon_state("array/prefix-sums", problems_dir=examples)
    assert state.problem_id == "array--prefix-sums"
    assert (examples / "array" / "prefix-sums" / "validator.cpp").exists()


def test_unlinked_problem_with_taken_problem_id_is_an_error_and_writes_nothing(
    problems_dir, polygon
):
    (problems_dir / "2025" / PROBLEM_ID / "authoring").mkdir(parents=True)
    (problems_dir / "2025" / PROBLEM_ID / "authoring" / "spec.yaml").write_text(
        "", encoding="utf-8"
    )

    with pytest.raises(PolygonStepError, match="already used by"):
        pull_problem(f"2026/{PROBLEM_ID}", problems_dir=problems_dir, client=polygon)

    assert polygon.calls == []
    assert not (problems_dir / "2026").exists()


def test_ambiguous_name_is_an_error(problems_dir, polygon):
    polygon.problems = [
        {"id": 1, "name": PROBLEM_ID, "owner": "a"},
        {"id": 2, "name": PROBLEM_ID, "owner": "b"},
    ]

    with pytest.raises(PolygonStepError, match="several Polygon problems"):
        pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)


def test_search_always_sends_name_and_adds_id_only_when_given(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    assert polygon.calls[0] == ("problems.list", {"name": PROBLEM_ID, "showDeleted": "false"})

    (problems_dir / PROBLEM_ID / "polygon_state.json").unlink()
    polygon.calls.clear()
    pull_problem(PROBLEM_ID, polygon_id=POLYGON_ID, problems_dir=problems_dir, client=polygon)
    assert polygon.calls[0] == (
        "problems.list",
        {"name": PROBLEM_ID, "showDeleted": "false", "id": str(POLYGON_ID)},
    )


def test_explicit_polygon_id_picks_one_of_same_named_problems(problems_dir, polygon):
    polygon.problems = [
        {"id": 1, "name": PROBLEM_ID, "owner": "a"},
        {"id": POLYGON_ID, "name": PROBLEM_ID, "owner": "b"},
    ]

    result = pull_problem(
        PROBLEM_ID, polygon_id=POLYGON_ID, problems_dir=problems_dir, client=polygon
    )

    assert result.polygon_id == POLYGON_ID
    assert load_polygon_state(PROBLEM_ID, problems_dir=problems_dir).polygon_id == POLYGON_ID


@pytest.mark.parametrize(
    "problems",
    [
        [{"id": POLYGON_ID, "name": "another-name"}],
        [{"id": POLYGON_ID, "name": PROBLEM_ID, "deleted": True}],
        [{"id": 7, "name": PROBLEM_ID}],
    ],
    ids=["name mismatch", "deleted", "other id"],
)
def test_explicit_polygon_id_still_requires_name_and_not_deleted(problems_dir, polygon, problems):
    polygon.problems = problems

    with pytest.raises(PolygonStepError, match=f"with id={POLYGON_ID}"):
        pull_problem(PROBLEM_ID, polygon_id=POLYGON_ID, problems_dir=problems_dir, client=polygon)

    assert not (problems_dir / PROBLEM_ID).exists()


def test_custom_checker_is_downloaded_and_not_treated_as_generator(problems_dir, polygon):
    polygon.checker = "check.cpp"
    polygon.source_files["check.cpp"] = b"// checker\n"

    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    problem_dir = problems_dir / PROBLEM_ID
    assert (problem_dir / "checker.cpp").read_bytes() == b"// checker\n"
    assert [p.name for p in (problem_dir / "generators").iterdir()] == ["gen_rand.cpp"]


def test_failed_part_does_not_stop_the_others_but_keeps_old_revision(problems_dir, polygon):
    _link(problems_dir, revision=3)
    polygon.failing = {"problem.statements", "problem.viewSolution"}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert not result.ok
    assert statuses["statement"] == ITEM_ERROR
    assert statuses["solutions/ma_cpp_sum_author.cpp"] == ITEM_ERROR
    assert statuses["validator.cpp"] == ITEM_DOWNLOADED
    assert not (problems_dir / PROBLEM_ID / "solutions" / "ma_cpp_sum_author.cpp").exists()
    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    assert state.revision == 3
    assert "validator.cpp" in state.synced
    assert "solutions/ma_cpp_sum_author.cpp" not in state.synced


def test_missing_parts_on_polygon_are_reported_as_absent(problems_dir, polygon):
    polygon.statements = {}
    polygon.validator = ""
    polygon.source_files = {}
    polygon.script = b""
    polygon.solutions = {}
    polygon.tests = []

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert result.ok
    for name in (
        "statement",
        "statement/examples",
        "validator.cpp",
        "generators",
        "test_script",
        "solutions",
    ):
        assert statuses[name] == ITEM_ABSENT, name
    assert not (problems_dir / PROBLEM_ID / "statement").exists()


def test_solution_name_outside_naming_scheme_gives_warning(problems_dir, polygon):
    polygon.solutions = {"sol.cpp": ("MA", b"// sol\n")}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert (problems_dir / PROBLEM_ID / "solutions" / "sol.cpp").exists()
    assert len(result.warnings) == 1 and "sol.cpp" in result.warnings[0]


def test_unsafe_file_name_from_polygon_is_not_written(problems_dir, polygon):
    polygon.solutions = {"../escape.cpp": ("OK", b"x")}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["solutions/../escape.cpp"] == ITEM_ERROR
    assert not (problems_dir / PROBLEM_ID / "escape.cpp").exists()


def test_cli_pull_needs_no_spec_and_logs_to_polygon_log(
    tmp_path, problems_dir, polygon, capsys, monkeypatch
):
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    exit_code = cli.main(
        [
            "--base-dir",
            str(problems_dir.parent),
            f"problems/{PROBLEM_ID}",
            "polygon",
            "pull",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "  validator.cpp..." in out  # progress by parts
    pulled = out.split("=== pulled ===\n")[1].split("\n\n")[0].splitlines()
    assert "validator.cpp" in pulled and "statement/ru/legend.tex" in pulled
    # What matches Polygon and what's absent on both sides isn't printed.
    assert "up to date" not in out and "absent" not in out
    assert (
        f"found Polygon problem named {PROBLEM_ID!r} (id={POLYGON_ID}), linked, revision 5" in out
    )
    log = (problems_dir / PROBLEM_ID / "polygon.log").read_text(encoding="utf-8")
    assert "run started: orchestrator --base-dir" in log
    assert "[pull] validator.cpp: downloaded" in log


def test_cli_pull_unresolved_problem_exits_with_error(problems_dir, polygon, capsys, monkeypatch):
    polygon.problems = []
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    exit_code = cli.main(
        ["--base-dir", str(problems_dir.parent), f"problems/{PROBLEM_ID}", "polygon", "pull"]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "no Polygon problem named" in err
    # an own error is printed in full, without a reference to the log
    assert err.strip().endswith("is available to this API key")


def test_cli_pull_overwrite_reports_backup_dir(problems_dir, polygon, capsys, monkeypatch):
    _write(problems_dir / PROBLEM_ID, "validator.cpp", "local validator")
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    exit_code = cli.main(
        [
            "--base-dir",
            str(problems_dir.parent),
            f"problems/{PROBLEM_ID}",
            "polygon",
            "pull",
            "--overwrite",
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "=== pulled over local changes (--overwrite) ===\nvalidator.cpp\n" in out
    assert "replaced local files are backed up in" in out


def test_cli_pull_groups_files_to_push_and_conflicts(problems_dir, polygon, capsys, monkeypatch):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/ru/legend.tex", "local legend")  # no base — differs
    _write(problem_dir, "generators/gen_local.cpp", "// local\n")  # local only
    _write(problem_dir, "checker.cpp", "// local checker\n")  # local only, with custom_needed
    _write(problem_dir, "limits.yaml", "time_limit_seconds: 1\nmemory_limit_mb: 256\n")
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    exit_code = cli.main(
        ["--base-dir", str(problems_dir.parent), f"problems/{PROBLEM_ID}", "polygon", "pull"]
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    to_push = out.split("=== will be pushed ===\n")[1].split("\n\n")[0].splitlines()
    assert to_push == [
        "checker.cpp (if checker.custom_needed: true)",
        "generators/gen_local.cpp",
    ]
    conflicts = out.split("=== conflicts ===\n")[1].split("\n\n")[0].splitlines()
    assert conflicts[:2] == [
        "statement/ru/legend.tex — no sync base",
        "limits.yaml — no sync base; local 1000ms/256MB, Polygon 2000ms/256MB",
    ]
    assert "--overwrite" in conflicts[2]


# --- Incremental pull: file version stamps on Polygon ---

_STAMPED_KEYS = {
    "problem_lib.h",
    "validator.cpp",
    "generators/gen_rand.cpp",
    "solutions/ma_cpp_sum_author.cpp",
    "spec.yaml",
}


def _state(problems_dir: Path) -> PolygonState:
    return load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)


def _assert_stamps_paired(problems_dir: Path) -> None:
    """The base invariant: only keys with a hash have stamps."""
    state = _state(problems_dir)
    assert set(state.stamps) <= set(state.synced)


def _bump_revision(polygon: FakePolygon) -> None:
    """Someone committed on Polygon: the shortcut by revision doesn't work,
    the stamps decide."""
    polygon.problems[0]["revision"] += 1


def test_pull_records_stamps_for_listed_files(problems_dir, polygon):
    polygon.mtimes["val.cpp"] = 123

    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    stamps = _state(problems_dir).stamps
    assert set(stamps) == _STAMPED_KEYS
    assert stamps["validator.cpp"] == "val.cpp:123:13"
    _assert_stamps_paired(problems_dir)


def test_pull_does_not_download_files_unchanged_on_polygon(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert result.ok
    assert polygon.downloads() == []
    for name in ("validator.cpp", "generators/gen_rand.cpp", "solutions/ma_cpp_sum_author.cpp"):
        item = next(item for item in result.items if item.name == name)
        assert item.status == ITEM_SAME, name
        assert "not downloaded" in item.detail
    assert _state(problems_dir).revision == 6
    assert set(_state(problems_dir).stamps) == _STAMPED_KEYS


def test_pull_downloads_only_files_changed_on_polygon(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    polygon.solutions["ma_cpp_sum_author.cpp"] = ("MA", b"// ok, fixed on Polygon\n")
    polygon.mtimes["ma_cpp_sum_author.cpp"] = 200
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["ma_cpp_sum_author.cpp"]
    assert _statuses(result)["solutions/ma_cpp_sum_author.cpp"] == ITEM_UPDATED
    assert (result.backup_dir / "solutions" / "ma_cpp_sum_author.cpp").read_bytes() == b"// ok\n"
    solution = problems_dir / PROBLEM_ID / "solutions" / "ma_cpp_sum_author.cpp"
    assert solution.read_bytes() == b"// ok, fixed on Polygon\n"
    assert _state(problems_dir).stamps["solutions/ma_cpp_sum_author.cpp"].endswith(":200:24")


def test_locally_changed_file_unchanged_on_polygon_is_kept_without_download(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    _write(problems_dir / PROBLEM_ID, "generators/gen_rand.cpp", "// local edit\n")
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == []
    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_KEPT
    state = _state(problems_dir)
    # The base still has the version from Polygon — push uploads the local one.
    assert state.synced["generators/gen_rand.cpp"] == pull_module.digest(b"// gen\n")
    assert "generators/gen_rand.cpp" in state.stamps


def test_overwrite_keeps_file_changed_only_locally(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    _write(problems_dir / PROBLEM_ID, "generators/gen_rand.cpp", "// local edit\n")
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == []
    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_KEPT
    gen = problems_dir / PROBLEM_ID / "generators" / "gen_rand.cpp"
    assert gen.read_text() == "// local edit\n"
    assert result.backup_dir is None


def test_file_changed_on_both_sides_is_a_conflict_replaced_only_with_overwrite(
    problems_dir, polygon
):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    _write(problems_dir / PROBLEM_ID, "generators/gen_rand.cpp", "// local edit\n")
    polygon.source_files["gen_rand.cpp"] = b"// polygon edit\n"
    polygon.mtimes["gen_rand.cpp"] = 200
    gen = problems_dir / PROBLEM_ID / "generators" / "gen_rand.cpp"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_CONFLICT
    assert gen.read_text() == "// local edit\n"
    # The base has the version from Polygon: push uploads the local one over it.
    assert _synced(problems_dir)["generators/gen_rand.cpp"] == pull_module.digest(
        b"// polygon edit\n"
    )

    # The base is now the version from Polygon, so for the next pull the edit is
    # only local; the conflict is resolved by --overwrite before this pull.
    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)
    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_KEPT


def test_overwrite_replaces_file_changed_on_both_sides(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    _write(problems_dir / PROBLEM_ID, "generators/gen_rand.cpp", "// local edit\n")
    polygon.source_files["gen_rand.cpp"] = b"// polygon edit\n"
    polygon.mtimes["gen_rand.cpp"] = 200

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_OVERWRITTEN
    gen = problems_dir / PROBLEM_ID / "generators" / "gen_rand.cpp"
    assert gen.read_bytes() == b"// polygon edit\n"
    assert (result.backup_dir / "generators" / "gen_rand.cpp").read_text() == "// local edit\n"


def test_polygon_and_local_changes_to_different_files_are_both_kept(problems_dir, polygon):
    # The statement was fixed on Polygon, the test-script — locally: pull takes
    # the statement from Polygon, and push after it uploads only the test-script.
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    polygon.statements["russian"]["legend"] = "L, edited on Polygon"
    _write(problems_dir / PROBLEM_ID, "test_script", "gen_rand 2 > $\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert statuses["statement/ru/legend.tex"] == ITEM_UPDATED
    assert statuses["test_script"] == ITEM_KEPT
    problem_dir = problems_dir / PROBLEM_ID
    assert (problem_dir / "statement" / "ru" / "legend.tex").read_text() == "L, edited on Polygon"
    assert (problem_dir / "test_script").read_text() == "gen_rand 2 > $\n"
    synced = _synced(problems_dir)
    assert synced["statement/ru/legend.tex"] == pull_module.digest(b"L, edited on Polygon")
    assert synced["test_script"] == pull_module.digest(normalize_script(polygon.script))


def test_missing_local_file_is_downloaded_even_if_unchanged_on_polygon(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    (problems_dir / PROBLEM_ID / "validator.cpp").unlink()
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["val.cpp"]
    assert _statuses(result)["validator.cpp"] == ITEM_DOWNLOADED


def test_unchanged_revision_skips_downloads_for_state_without_stamps(problems_dir, polygon):
    # The problem was synced before stamps appeared: the revision is the same — nothing
    # was committed on Polygon, the base's hashes are correct even without stamps.
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    state = _state(problems_dir)
    save_polygon_state(PROBLEM_ID, replace(state, stamps={}), problems_dir=problems_dir)
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert result.ok
    assert polygon.downloads() == []
    assert set(_state(problems_dir).stamps) == _STAMPED_KEYS


def test_changed_revision_without_stamps_downloads_everything(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    state = _state(problems_dir)
    save_polygon_state(PROBLEM_ID, replace(state, stamps={}), problems_dir=problems_dir)
    _bump_revision(polygon)
    polygon.calls.clear()

    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert sorted(polygon.downloads()) == sorted(
        ["problem_lib.h", "val.cpp", "gen_rand.cpp", "ma_cpp_sum_author.cpp", "spec.yaml"]
    )


def test_files_without_modification_time_are_always_downloaded(problems_dir, polygon):
    polygon.mtimes["gen_rand.cpp"] = None
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    polygon.calls.clear()

    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["gen_rand.cpp"]
    assert "generators/gen_rand.cpp" not in _state(problems_dir).stamps


class _FailingDownload(FakePolygon):
    """A Polygon on which downloading individual files by name fails."""

    def __init__(self) -> None:
        super().__init__()
        self.failing_names: set[str] = set()

    def call_raw(self, method_name, params):
        if params.get("name") in self.failing_names:
            self.calls.append((method_name, params))
            raise PolygonApiError(method_name, "HTTP 429")
        return super().call_raw(method_name, params)


def test_failed_download_drops_hash_and_stamp_and_is_retried(problems_dir):
    polygon = _FailingDownload()
    polygon.failing_names = {"ma_cpp_sum_author.cpp"}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not result.ok
    state = _state(problems_dir)
    assert "solutions/ma_cpp_sum_author.cpp" not in state.synced
    assert "solutions/ma_cpp_sum_author.cpp" not in state.stamps
    assert state.revision is None
    _assert_stamps_paired(problems_dir)

    polygon.failing_names = set()
    polygon.calls.clear()
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    # The base's revision is unknown, the stamps decide: only what failed is downloaded.
    assert result.ok
    assert polygon.downloads() == ["ma_cpp_sum_author.cpp"]
    assert _state(problems_dir).revision == 5


def test_failed_listing_keeps_previous_pairs_valid(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    before = _state(problems_dir)
    _bump_revision(polygon)
    polygon.failing = {"problem.solutions"}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not result.ok
    after = _state(problems_dir)
    # The "solutions" part wasn't compared — its hash+stamp pair is from the previous pull.
    key = "solutions/ma_cpp_sum_author.cpp"
    assert after.synced[key] == before.synced[key]
    assert after.stamps[key] == before.stamps[key]
    assert after.revision == 5
    _assert_stamps_paired(problems_dir)

    # A solution was changed on Polygon while the listing was failing — the stamp won't match.
    polygon.failing = set()
    polygon.solutions["ma_cpp_sum_author.cpp"] = ("MA", b"// changed\n")
    polygon.mtimes["ma_cpp_sum_author.cpp"] = 300
    polygon.calls.clear()
    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["ma_cpp_sum_author.cpp"]
    assert _statuses(result)["solutions/ma_cpp_sum_author.cpp"] == ITEM_UPDATED


def _make_interactive(polygon: FakePolygon) -> None:
    polygon.info = {**polygon.info, "interactive": True}
    polygon.interactor = "inter.cpp"
    polygon.source_files["inter.cpp"] = b"// interactor\n"
    polygon.statements["russian"]["interaction"] = "Protocol"
    polygon.tests[0].update(inputForStatement="10\n\n<\n", outputForStatement="\n? 5\n\n")


def test_pull_interactive_problem(problems_dir, polygon):
    _make_interactive(polygon)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    problem_dir = problems_dir / PROBLEM_ID
    examples_dir = problem_dir / "statement" / "examples"
    assert result.ok
    assert (problem_dir / "interactor.cpp").read_bytes() == b"// interactor\n"
    # The interactor isn't a generator, even with a non-standard name.
    assert [p.name for p in (problem_dir / "generators").iterdir()] == ["gen_rand.cpp"]
    assert (problem_dir / "statement" / "ru" / "interaction.tex").read_text() == "Protocol"
    assert (examples_dir / "example_1.input_for_statement.txt").read_text() == "10\n\n<\n"
    assert (examples_dir / "example_1.output_for_statement.txt").read_text() == "\n? 5\n\n"
    # The second example has no dialogue — there are no files, and the report doesn't mention it.
    assert not (examples_dir / "example_2.input_for_statement.txt").exists()
    statuses = _statuses(result)
    assert "statement/examples/example_2.input_for_statement.txt" not in statuses
    assert statuses["statement/examples/example_1.txt"] == ITEM_DOWNLOADED
    synced = _synced(problems_dir)
    assert {"interactive", "interactor", "interactor.cpp"} <= set(synced)


def test_pull_plain_problem_statement_output_and_verify_flag(problems_dir, polygon):
    polygon.tests[0].update(outputForStatement="42\n", verifyInputOutputForStatements=True)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    examples_dir = problems_dir / PROBLEM_ID / "statement" / "examples"
    assert result.ok
    assert (examples_dir / "example_1.output_for_statement.txt").read_text() == "42\n"
    # Only one field is set — there's no second file.
    assert not (examples_dir / "example_1.input_for_statement.txt").exists()
    assert (examples_dir / "example_1.verify_for_statement.txt").read_text() == "true\n"
    assert not (examples_dir / "example_2.verify_for_statement.txt").exists()
    statuses = _statuses(result)
    assert "statement/examples/example_2.verify_for_statement.txt" not in statuses

    # The flag was turned off on Polygon — the local file stays and shows as local only.
    polygon.tests[0]["verifyInputOutputForStatements"] = False
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert (examples_dir / "example_1.verify_for_statement.txt").exists()
    assert _statuses(result)["statement/examples/example_1.verify_for_statement.txt"] == (
        ITEM_LOCAL_ONLY
    )


def test_pull_downloads_tutorial_and_push_sees_no_changes(problems_dir, polygon):
    polygon.statements["russian"]["tutorial"] = "Tutorial\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    path = problems_dir / PROBLEM_ID / "statement" / "ru" / "tutorial.tex"
    assert result.ok
    assert path.read_text() == "Tutorial\n"
    assert _statuses(result)["statement/ru/tutorial.tex"] == ITEM_DOWNLOADED
    assert upload_statement.STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    # An edit on Polygon with an unchanged local file — a fast-forward with a backup.
    polygon.statements["russian"]["tutorial"] = "New tutorial\n"
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["statement/ru/tutorial.tex"] == ITEM_UPDATED
    assert path.read_text() == "New tutorial\n"
    assert (result.backup_dir / "statement" / "ru" / "tutorial.tex").read_text() == "Tutorial\n"

    # An edit on both sides — a conflict, the local version stays.
    path.write_text("Local tutorial\n")
    polygon.statements["russian"]["tutorial"] = "Newer tutorial\n"
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["statement/ru/tutorial.tex"] == ITEM_CONFLICT
    assert path.read_text() == "Local tutorial\n"


def test_pull_without_tutorial_on_polygon(problems_dir, polygon):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    path = problems_dir / PROBLEM_ID / "statement" / "ru" / "tutorial.tex"
    assert not path.exists()
    assert "statement/ru/tutorial.tex" not in _statuses(result)

    path.write_text("Local tutorial\n")
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "statement/ru/tutorial.tex")
    assert item.status == ITEM_LOCAL_ONLY
    assert item.push_condition == ""
    assert path.read_text() == "Local tutorial\n"
    assert "statement/ru/tutorial.tex" not in _synced(problems_dir)


def test_pull_plain_problem_does_not_ask_for_interactor(problems_dir, polygon):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert "problem.interactor" not in polygon.methods()
    statuses = _statuses(result)
    assert "interactor.cpp" not in statuses
    assert "statement/ru/interaction.tex" not in statuses


def test_pull_plain_problem_reports_local_interactor(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "interactor.cpp", "// local\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["interactor.cpp"] == ITEM_LOCAL_ONLY


def test_push_after_pull_of_interactive_problem_uploads_nothing(problems_dir, polygon):
    # The pull and push hashes for identical contents match — push after
    # pull uploads nothing (the spec is interactive, like the problem on Polygon).
    # Push would re-upload an interactor with another name under `interactor.cpp`,
    # like the checker — here it's already under this name.
    _make_interactive(polygon)
    polygon.interactor = "interactor.cpp"
    polygon.source_files["interactor.cpp"] = polygon.source_files.pop("inter.cpp")
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    spec = load_spec(FIXTURES_DIR / "interactive-spec.yaml")
    client = MagicMock()

    for step in (set_constraints, upload_interactor, upload_statement):
        result = run_step(
            step.STEP,
            PROBLEM_ID,
            spec,
            problems_dir=problems_dir,
            client=client,
            synced=_synced(problems_dir),
        )
        assert result.uploaded == {}, step.STEP_NAME

    client.call.assert_not_called()


def test_cli_pull_prints_warnings_as_a_group_after_progress(
    problems_dir, polygon, capsys, monkeypatch
):
    polygon.solutions = {"sol.cpp": ("MA", b"// sol\n")}
    monkeypatch.setattr(pull_module, "PolygonClient", lambda: polygon)

    cli.main(["--base-dir", str(problems_dir.parent), f"problems/{PROBLEM_ID}", "polygon", "pull"])

    out = capsys.readouterr().out
    warnings = out.split("=== warnings ===\n")[1].split("\n\n")[0].splitlines()
    assert len(warnings) == 1 and warnings[0].startswith("solutions/sol.cpp: the name does not")
    assert out.index("  solutions...") < out.index("=== warnings ===")


# --- statement languages -----------------------------------------------------


def test_pull_puts_every_language_into_its_directory(problems_dir, polygon):
    polygon.statements["english"] = {
        "name": "English Name",
        "legend": "EL",
        "input": "EI",
        "output": "EO",
        "notes": "",
        "tutorial": "ET\n",
    }
    polygon.statements["latvian"] = {
        "name": "LV",
        "legend": "LL",
        "input": "",
        "output": "",
        "notes": "",
    }

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statement_dir = problems_dir / PROBLEM_ID / "statement"
    assert result.ok
    assert (statement_dir / "en" / "legend.tex").read_text() == "EL"
    assert (statement_dir / "en" / "notes.tex").read_text() == ""
    assert (statement_dir / "en" / "tutorial.tex").read_text() == "ET\n"
    assert (statement_dir / "en" / "name.txt").read_text() == "English Name\n"
    # A language without a code — a directory with the Polygon name.
    assert (statement_dir / "latvian" / "legend.tex").read_text() == "LL"
    assert (statement_dir / "ru" / "legend.tex").read_text() == "L"
    assert sorted(path.name for path in statement_dir.iterdir()) == [
        "en",
        "examples",
        "latvian",
        "ru",
    ]
    assert _statuses(result)["statement/en/legend.tex"] == ITEM_DOWNLOADED
    assert "statement/en/name" in _synced(problems_dir)
    assert upload_statement.STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_local_language_missing_on_polygon_is_local_only(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _write(problems_dir / PROBLEM_ID, "statement/en/legend.tex", "local")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert statuses["statement/en/legend.tex"] == ITEM_LOCAL_ONLY
    # The report doesn't mention files that exist neither here nor there.
    assert "statement/en/notes.tex" not in statuses
    assert (problems_dir / PROBLEM_ID / "statement/en/legend.tex").read_text() == "local"


def test_pull_downloads_scoring(problems_dir, polygon):
    polygon.statements["russian"]["scoring"] = "Subtasks\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    path = problems_dir / PROBLEM_ID / "statement" / "ru" / "scoring.tex"
    assert path.read_text() == "Subtasks\n"
    assert _statuses(result)["statement/ru/scoring.tex"] == ITEM_DOWNLOADED
    assert upload_statement.STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


_CONSTRAINTS_WITH_GROUPS = """\
test_groups:
  - name: "1"
    points: 40
    feedback_policy: icpc
  - name: "2"
    points: 60
    dependencies: ["1"]
    feedback_policy: icpc
"""


def _grouped_polygon(polygon) -> None:
    """Tests and groups on Polygon as `upload_test_groups` sets them by
    `_CONSTRAINTS_WITH_GROUPS`."""
    polygon.aux_files["constraints.yaml"] = _CONSTRAINTS_WITH_GROUPS.encode()
    polygon.tests = [
        {"index": 1, "manual": True, "input": "1 2\n", "useInStatements": True, "group": "0"},
        {"index": 2, "manual": False, "scriptLine": "gen 1 -group=1", "group": "1", "points": 40},
        {"index": 3, "manual": False, "scriptLine": "gen 2 -group=2", "group": "2", "points": 60},
    ]
    polygon.test_groups = [
        {"name": "0", "pointsPolicy": "COMPLETE_GROUP", "feedbackPolicy": "COMPLETE"},
        {"name": "1", "pointsPolicy": "COMPLETE_GROUP", "feedbackPolicy": "ICPC"},
        {
            "name": "2",
            "pointsPolicy": "COMPLETE_GROUP",
            "feedbackPolicy": "ICPC",
            "dependencies": ["1"],
        },
    ]


def test_pull_without_local_groups_does_not_ask_for_them(problems_dir, polygon):
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert result.ok
    assert "problem.viewTestGroup" not in polygon.methods()
    assert "test groups" not in _statuses(result)


def test_pull_matching_groups_lets_push_skip_them(problems_dir, polygon):
    _grouped_polygon(polygon)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert result.ok
    assert _statuses(result)["test groups"] == ITEM_SAME
    synced = _synced(problems_dir)
    assert synced["test_groups"] and synced["test_groups/tests"]
    assert upload_test_groups.STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"

    # Push compares with the same base: nothing to send but the tests listing.
    client = MagicMock()
    client.call.return_value = polygon.tests
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    result = run_step(
        upload_test_groups.STEP,
        PROBLEM_ID,
        spec,
        problems_dir=problems_dir,
        client=client,
        synced=dict(state.synced),
    )
    assert "no changes" in result.message
    assert [c.args[0] for c in client.call.call_args_list] == ["problem.tests"]


def _constraints_text(problems_dir) -> str:
    return (problems_dir / PROBLEM_ID / "authoring" / "constraints.yaml").read_text()


def test_pull_writes_groups_changed_only_on_polygon(problems_dir, polygon):
    _grouped_polygon(polygon)
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    polygon.tests[2]["points"] = 50
    polygon.test_groups[2]["feedbackPolicy"] = "POINTS"
    polygon.problems[0]["revision"] = 6
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "test groups")
    assert item.status == ITEM_UPDATED
    assert "2 (50, POINTS, after 1)" in item.detail and "2 (60, ICPC, after 1)" in item.detail
    text = _constraints_text(problems_dir)
    assert '  - name: "2"\n    points: 50\n    dependencies: ["1"]\n' in text
    assert "    feedback_policy: points\n" in text
    assert text.startswith(_CONSTRAINTS_WITH_GROUPS.split("test_groups:")[0])
    # The groups now match Polygon: push has nothing to set.
    assert upload_test_groups.STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_pull_writes_added_and_removed_groups(problems_dir, polygon):
    _grouped_polygon(polygon)
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    polygon.tests[2].update(group="3", points=60)
    polygon.test_groups[2] = {
        "name": "3",
        "pointsPolicy": "COMPLETE_GROUP",
        "feedbackPolicy": "ICPC",
        "dependencies": ["1"],
    }
    polygon.problems[0]["revision"] = 6
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["test groups"] == ITEM_UPDATED
    text = _constraints_text(problems_dir)
    assert 'name: "2"' not in text
    assert (
        '  - name: "3"\n    points: 60\n    dependencies: ["1"]\n'
        "    feedback_policy: icpc\n    status: confirmed\n"
    ) in text


def test_pull_keeps_groups_changed_on_both_sides(problems_dir, polygon):
    _grouped_polygon(polygon)
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    path = problems_dir / PROBLEM_ID / "authoring" / "constraints.yaml"
    local = path.read_text().replace("points: 40", "points: 30").replace("points: 60", "points: 70")
    path.write_text(local)

    polygon.tests[2]["points"] = 50
    polygon.problems[0]["revision"] = 6
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["test groups"] == ITEM_CONFLICT
    assert path.read_text() == local

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon, overwrite=True)

    assert _statuses(result)["test groups"] == ITEM_OVERWRITTEN
    assert "points: 50" in path.read_text() and "points: 40" in path.read_text()


def test_pull_writes_an_each_test_group(problems_dir, polygon):
    _grouped_polygon(polygon)
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    polygon.test_groups[1]["pointsPolicy"] = "EACH_TEST"
    polygon.problems[0]["revision"] = 6
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "test groups")
    assert item.status == ITEM_UPDATED
    assert "1 (40, ICPC, EACH_TEST)" in item.detail
    text = _constraints_text(problems_dir)
    first = text.split('  - name: "2"')[0]
    assert "    points_policy: each_test\n" in first
    # A complete_group group without the field stays without it.
    assert text.count("points_policy") == 1
    assert upload_test_groups.STEP.compute_status(PROBLEM_ID, problems_dir)[0] == "done"


def test_pull_does_not_write_groups_it_cannot_hold(problems_dir, polygon):
    _grouped_polygon(polygon)
    polygon.tests.append(
        {"index": 4, "manual": False, "scriptLine": "gen 3 -group=1", "group": "1", "points": 0}
    )
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    before = _constraints_text(problems_dir)

    polygon.test_groups[1]["pointsPolicy"] = "EACH_TEST"
    polygon.problems[0]["revision"] = 6
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "test groups")
    assert item.status == ITEM_DIFFERS
    assert "EACH_TEST with tests of different points" in item.detail
    assert _constraints_text(problems_dir) == before


def test_pull_groups_not_enabled_on_polygon(problems_dir, polygon):
    _grouped_polygon(polygon)
    polygon.test_groups = None
    for test in polygon.tests:
        test.pop("group")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert result.ok
    item = next(item for item in result.items if item.name == "test groups")
    assert item.status == ITEM_DIFFERS
    assert "Polygon: no groups" in item.detail
    assert _synced(problems_dir).get("test_groups/tests") is None


def test_pull_reports_other_errors_reading_groups(problems_dir, polygon):
    _grouped_polygon(polygon)
    polygon.test_groups = "Internal error"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not result.ok
    assert _statuses(result)["test groups"] == ITEM_ERROR
