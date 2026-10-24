from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from orchestrator import cli
from orchestrator.polygon import pull as pull_module
from orchestrator.polygon.client import PolygonApiError
from orchestrator.polygon.pull import (
    ITEM_ABSENT,
    ITEM_DOWNLOADED,
    ITEM_ERROR,
    ITEM_KEPT,
    ITEM_LOCAL_ONLY,
    ITEM_OVERWRITTEN,
    ITEM_SAME,
    pull_problem,
)
from orchestrator.polygon.state import PolygonState, load_polygon_state, save_polygon_state
from orchestrator.polygon.steps import (
    set_constraints,
    upload_generators,
    upload_problem_lib,
    upload_solutions,
    upload_spec,
    upload_statement,
    upload_test_script,
    upload_validator,
)
from orchestrator.polygon.steps.base import PolygonStepError, run_step
from orchestrator.polygon.steps.upload_test_script import normalize_script
from orchestrator.steps.base import template_problem_lib_path
from orchestrator.spec import load_spec

FIXTURES_DIR = Path(__file__).parent / "fixtures"

PROBLEM_ID = "example-problem"
POLYGON_ID = 4242


class FakePolygon:
    """Задача на Polygon в памяти: отвечает на те методы, которые вызывает `pull`."""

    def __init__(self) -> None:
        self.problems = [
            {"id": POLYGON_ID, "name": PROBLEM_ID, "owner": "author", "revision": 5, "modified": False}
        ]
        self.statements = {
            "russian": {"name": "Polygon Name", "legend": "L", "input": "I", "output": "O", "notes": ""}
        }
        self.info = {"timeLimit": 2000, "memoryLimit": 256}
        self.validator = "val.cpp"
        self.checker = "std::ncmp.cpp"
        self.source_files = {"val.cpp": b"// validator\n", "gen_rand.cpp": b"// gen\n"}
        self.resource_files = {"problem_lib.h": template_problem_lib_path().read_bytes()}
        self.aux_files = {"spec.yaml": b"problem_id: example-problem\n"}
        self.script = b"gen_rand 1 > $\n"
        self.solutions = {"ok_cpp_main_author.cpp": ("MA", b"// ok\n")}
        self.tests = [
            {"index": 1, "manual": True, "input": "1 2\n", "useInStatements": True},
            {"index": 2, "manual": False, "useInStatements": True},
            {"index": 3, "manual": False, "useInStatements": False},
        ]
        self.test_inputs = {2: b"3 4\n"}
        # Время изменения файлов и решений в листингах (по умолчанию 100);
        # `None` — Polygon его не отдаёт.
        self.mtimes: dict[str, int | None] = {}
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
                if params.get("name", "") in p["name"] and params.get("id") in (None, str(p["id"]))
            ]
        assert params["problemId"] == str(POLYGON_ID)
        return {
            "problem.statements": lambda: self.statements,
            "problem.info": lambda: self.info,
            "problem.validator": lambda: self.validator,
            "problem.checker": lambda: self.checker,
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
        }[method_name]()

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
        """Скачанные файлы с отметками: source/resource/aux-файлы и решения."""
        return [
            params["name"]
            for name, params in self.calls
            if name in ("problem.viewFile", "problem.viewSolution")
        ]


@pytest.fixture
def problems_dir(tmp_path):
    return tmp_path / "problems"


@pytest.fixture
def polygon():
    return FakePolygon()


def _statuses(result) -> dict[str, str]:
    return {item.name: item.status for item in result.items}


def _link(problems_dir: Path, polygon_id: int = POLYGON_ID, *, revision: int | None = None) -> None:
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=polygon_id,
            created_at="2026-09-19T12:00:00+00:00",
            revision=revision,
            synced={"stale/key": "abc"},
        ),
        problems_dir=problems_dir,
    )


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
    assert (problem_dir / "statement" / "legend.tex").read_text() == "L"
    assert (problem_dir / "statement" / "input_format.tex").read_text() == "I"
    assert (problem_dir / "statement" / "output_format.tex").read_text() == "O"
    # Пустой раздел выгружается пустым файлом — upload_statement требует все 4.
    assert (problem_dir / "statement" / "notes.tex").read_text() == ""
    assert (problem_dir / "statement" / "name.txt").read_text() == "Polygon Name\n"
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
    assert (problem_dir / "problem_lib.h").read_bytes() == polygon.resource_files["problem_lib.h"]
    assert (problem_dir / "spec.yaml").read_bytes() == polygon.aux_files["spec.yaml"]
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
        "solutions/ok_cpp_main_author.cpp",
        "spec.yaml",
        "statement/examples/example_1.txt",
        "statement/examples/example_2.txt",
        "statement/input_format.tex",
        "statement/legend.tex",
        "statement/name",
        "statement/notes.tex",
        "statement/output_format.tex",
        "test_script",
        "validator",
        "validator.cpp",
    ]


def test_push_after_pull_uploads_only_what_differs_from_polygon(tmp_path, problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    # upload_test_script отклоняет groups — нужен flat
    spec_path = tmp_path / "valid-spec.yaml"
    spec_path.write_text(
        (FIXTURES_DIR / "valid-spec.yaml")
        .read_text(encoding="utf-8")
        .replace('script_style: "groups"', 'script_style: "flat"'),
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
        upload_problem_lib.STEP,
        upload_generators.STEP,
        upload_test_script.STEP,
        upload_solutions.STEP,
        upload_spec.STEP,
    ):
        assert uploaded(step) == {}, step.name
    # на Polygon валидатор назначен под другим именем — push загрузит validator.cpp
    assert set(uploaded(upload_validator.STEP)) == {"validator", "validator.cpp"}


def test_name_is_compared_without_surrounding_whitespace(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/name.txt", "  Polygon Name")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["statement/name.txt"] == ITEM_SAME
    assert _synced(problems_dir)["statement/name"] == pull_module.digest("Polygon Name")


def test_name_renamed_on_polygon_is_not_pushed_back_after_pull(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/name.txt", "Old Name\n")

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)
    assert _statuses(result)["statement/name.txt"] == ITEM_OVERWRITTEN

    # title.ru спека ("Кратчайшие пути") больше не источник названия
    spec = load_spec(FIXTURES_DIR / "valid-spec.yaml")
    uploaded = run_step(
        upload_statement.STEP,
        PROBLEM_ID,
        spec,
        problems_dir=problems_dir,
        client=MagicMock(),
        synced=_synced(problems_dir),
    ).uploaded
    assert "statement/name" not in uploaded


def test_script_without_blank_lines_on_polygon_is_same_and_not_pushed_again(
    tmp_path, problems_dir, polygon
):
    # Polygon выкидывает пустые строки из сохранённого скрипта.
    problem_dir = problems_dir / PROBLEM_ID
    local = "gen_rand 1 > $\n\n\ngen_rand 2 > $\n"
    _write(problem_dir, "test_script", local)
    polygon.script = b"gen_rand 1 > $\ngen_rand 2 > $\n"

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["test_script"] == ITEM_SAME
    assert (problem_dir / "test_script").read_text() == local
    spec_path = tmp_path / "valid-spec.yaml"
    spec_path.write_text(
        (FIXTURES_DIR / "valid-spec.yaml")
        .read_text(encoding="utf-8")
        .replace('script_style: "groups"', 'script_style: "flat"'),
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

    content = (problems_dir / PROBLEM_ID / "constraints.yaml").read_text()
    assert "time_limit_seconds: 1.5\n" in content


def test_differing_files_are_kept_with_polygon_version_in_baseline(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/legend.tex", "local legend")
    _write(problem_dir, "statement/examples/example_1.txt", "1 2\n")
    _write(problem_dir, "validator.cpp", "local validator")
    _write(problem_dir, "test_script_groups", "local script")
    _write(problem_dir, "solutions/ok_cpp_main_author.cpp", "// ok\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert statuses["statement/legend.tex"] == ITEM_KEPT
    assert "the next push will upload it" in next(
        item.detail for item in result.items if item.name == "statement/legend.tex"
    )
    assert statuses["statement/input_format.tex"] == ITEM_DOWNLOADED
    assert statuses["statement/examples/example_1.txt"] == ITEM_SAME
    assert statuses["validator.cpp"] == ITEM_KEPT
    assert statuses["test_script_groups"] == ITEM_KEPT
    assert statuses["solutions/ok_cpp_main_author.cpp"] == ITEM_SAME
    assert (problem_dir / "statement" / "legend.tex").read_text() == "local legend"
    assert (problem_dir / "validator.cpp").read_text() == "local validator"
    assert not (problem_dir / "test_script").exists()
    synced = _synced(problems_dir)
    # В базе — версия с Polygon: push сравнит с ней локальную и загрузит.
    assert synced["statement/legend.tex"] == pull_module.digest("L")
    assert synced["validator.cpp"] == pull_module.digest(b"// validator\n")
    assert synced["test_script"] == pull_module.digest(normalize_script(polygon.script))
    # Назначение валидатора не подтверждено — push назначит заново.
    assert "validator" not in synced
    for key in ("statement/input_format.tex", "statement/examples/example_1.txt"):
        assert key in synced, key
    assert result.backup_dir is None


def test_overwrite_replaces_differing_files_and_backs_them_up(problems_dir, polygon):
    problem_dir = problems_dir / PROBLEM_ID
    _write(problem_dir, "statement/legend.tex", "local legend")
    _write(problem_dir, "validator.cpp", "local validator")
    _write(problem_dir, "generators/gen_rand.cpp", "// gen\n")

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    statuses = _statuses(result)
    assert statuses["statement/legend.tex"] == ITEM_OVERWRITTEN
    assert statuses["validator.cpp"] == ITEM_OVERWRITTEN
    assert statuses["generators/gen_rand.cpp"] == ITEM_SAME
    assert (problem_dir / "statement" / "legend.tex").read_text() == "L"
    assert (problem_dir / "validator.cpp").read_bytes() == b"// validator\n"
    assert result.backup_dir.parent == problem_dir / ".pull_backup"
    assert (result.backup_dir / "statement" / "legend.tex").read_text() == "local legend"
    assert (result.backup_dir / "validator.cpp").read_text() == "local validator"
    assert not (result.backup_dir / "generators").exists()
    assert {"statement/legend.tex", "validator.cpp", "validator"} <= set(_synced(problems_dir))


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
    assert "not on Polygon" in next(
        item.detail for item in result.items if item.name == "generators/gen_local.cpp"
    )


def test_constraints_pick_result_is_never_overwritten(problems_dir, polygon):
    path = _write(
        problems_dir / PROBLEM_ID,
        "constraints.yaml",
        "limits:\n  time_limit_seconds: 1\n  memory_limit_mb: 256\ngroups: []\n",
    )

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "constraints.yaml")
    assert item.status == ITEM_KEPT
    assert "1000ms/256MB" in item.detail and "2000ms/256MB" in item.detail
    assert "manually" in item.detail
    assert "groups: []" in path.read_text()
    assert "limits" not in _synced(problems_dir)


def test_pulled_constraints_file_is_overwritten(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    polygon.info = {"timeLimit": 3000, "memoryLimit": 256}

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["constraints.yaml"] == ITEM_OVERWRITTEN
    assert set_constraints.read_limits(PROBLEM_ID, problems_dir) == (3000, 256)


def test_same_limits_in_constraints_pick_result_are_up_to_date(problems_dir, polygon):
    _write(
        problems_dir / PROBLEM_ID,
        "constraints.yaml",
        "time_limit_seconds: 2\nmemory_limit_mb: 256\ngroups: []\n",
    )

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["constraints.yaml"] == ITEM_SAME
    assert "limits" in _synced(problems_dir)


def test_problem_lib_from_polygon_is_downloaded_even_if_it_differs_from_template(
    problems_dir, polygon
):
    polygon.resource_files["problem_lib.h"] = b"// edited for this problem\n"

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["problem_lib.h"] == ITEM_DOWNLOADED
    assert (problems_dir / PROBLEM_ID / "problem_lib.h").read_bytes() == b"// edited for this problem\n"
    assert _synced(problems_dir)["problem_lib.h"] == pull_module.digest(b"// edited for this problem\n")


def test_different_local_problem_lib_is_kept(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "problem_lib.h", "// local edits\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["problem_lib.h"] == ITEM_KEPT
    assert (problems_dir / PROBLEM_ID / "problem_lib.h").read_text() == "// local edits\n"
    assert _synced(problems_dir)["problem_lib.h"] == pull_module.digest(
        polygon.resource_files["problem_lib.h"]
    )


def test_problem_lib_missing_on_polygon(problems_dir, polygon):
    polygon.resource_files = {}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["problem_lib.h"] == ITEM_ABSENT
    assert "problem_lib.h" not in _synced(problems_dir)


def test_different_local_spec_is_kept(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "spec.yaml", "problem_id: example-problem\n# local\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["spec.yaml"] == ITEM_KEPT
    assert _synced(problems_dir)["spec.yaml"] == pull_module.digest(polygon.aux_files["spec.yaml"])


def test_spec_missing_on_polygon(problems_dir, polygon):
    polygon.aux_files = {}
    _write(problems_dir / PROBLEM_ID, "spec.yaml", "problem_id: example-problem\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert _statuses(result)["spec.yaml"] == ITEM_LOCAL_ONLY
    assert "spec.yaml" not in _synced(problems_dir)


def test_local_checker_with_standard_checker_on_polygon(problems_dir, polygon):
    _write(problems_dir / PROBLEM_ID, "checker.cpp", "// local checker\n")

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    item = next(item for item in result.items if item.name == "checker.cpp")
    assert item.status == ITEM_LOCAL_ONLY
    assert "std::ncmp.cpp" in item.detail and "custom_needed" in item.detail
    synced = _synced(problems_dir)
    assert "checker" in synced and "checker.cpp" not in synced


def test_existing_link_is_reused_without_search_and_baseline_is_replaced(problems_dir, polygon):
    _link(problems_dir)

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.calls[0] == ("problems.list", {"id": str(POLYGON_ID)})
    assert [method for method in polygon.methods() if method == "problems.list"] == ["problems.list"]
    assert result.link_detail == f"already linked to Polygon id={POLYGON_ID}"
    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    assert state.created_at == "2026-09-19T12:00:00+00:00"
    assert state.revision == 5
    # чужие ключи pull не трогает — он переписывает только то, что сравнил
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
    assert ("problems.list", {"name": "array--prefix-sums", "showDeleted": "false"}) in polygon.calls
    state = load_polygon_state("array/prefix-sums", problems_dir=examples)
    assert state.problem_id == "array--prefix-sums"
    assert (examples / "array" / "prefix-sums" / "validator.cpp").exists()


def test_unlinked_problem_with_taken_problem_id_is_an_error_and_writes_nothing(
    problems_dir, polygon
):
    (problems_dir / "2025" / PROBLEM_ID).mkdir(parents=True)
    (problems_dir / "2025" / PROBLEM_ID / "spec.yaml").write_text("", encoding="utf-8")

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
    assert statuses["solutions/ok_cpp_main_author.cpp"] == ITEM_ERROR
    assert statuses["validator.cpp"] == ITEM_DOWNLOADED
    assert not (problems_dir / PROBLEM_ID / "solutions" / "ok_cpp_main_author.cpp").exists()
    state = load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)
    assert state.revision == 3
    assert "validator.cpp" in state.synced
    assert "solutions/ok_cpp_main_author.cpp" not in state.synced


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
        "statement/legend.tex",
        "statement/notes.tex",
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
    assert "  validator.cpp: downloaded — Polygon source file val.cpp" in out
    assert f"found Polygon problem named {PROBLEM_ID!r} (id={POLYGON_ID}), linked, revision 5" in out
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
    # собственная ошибка печатается целиком, без ссылки на лог
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
    assert "  validator.cpp: overwritten" in out
    assert "overwritten local files are backed up in" in out


# --- Инкрементальный pull: отметки версий файлов на Polygon ---

_STAMPED_KEYS = {
    "problem_lib.h",
    "validator.cpp",
    "generators/gen_rand.cpp",
    "solutions/ok_cpp_main_author.cpp",
    "spec.yaml",
}


def _state(problems_dir: Path) -> PolygonState:
    return load_polygon_state(PROBLEM_ID, problems_dir=problems_dir)


def _assert_stamps_paired(problems_dir: Path) -> None:
    """Инвариант базы: отметки бывают только у ключей с хешем."""
    state = _state(problems_dir)
    assert set(state.stamps) <= set(state.synced)


def _bump_revision(polygon: FakePolygon) -> None:
    """Кто-то закоммитил на Polygon: короткий путь по ревизии не работает,
    решают отметки."""
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
    for name in ("validator.cpp", "generators/gen_rand.cpp", "solutions/ok_cpp_main_author.cpp"):
        item = next(item for item in result.items if item.name == name)
        assert item.status == ITEM_SAME, name
        assert "not downloaded" in item.detail
    assert _state(problems_dir).revision == 6
    assert set(_state(problems_dir).stamps) == _STAMPED_KEYS


def test_pull_downloads_only_files_changed_on_polygon(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    polygon.solutions["ok_cpp_main_author.cpp"] = ("MA", b"// ok, fixed on Polygon\n")
    polygon.mtimes["ok_cpp_main_author.cpp"] = 200
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["ok_cpp_main_author.cpp"]
    assert _statuses(result)["solutions/ok_cpp_main_author.cpp"] == ITEM_OVERWRITTEN
    solution = problems_dir / PROBLEM_ID / "solutions" / "ok_cpp_main_author.cpp"
    assert solution.read_bytes() == b"// ok, fixed on Polygon\n"
    assert _state(problems_dir).stamps["solutions/ok_cpp_main_author.cpp"].endswith(":200:24")


def test_locally_changed_file_unchanged_on_polygon_is_kept_without_download(
    problems_dir, polygon
):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    _write(problems_dir / PROBLEM_ID, "generators/gen_rand.cpp", "// local edit\n")
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == []
    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_KEPT
    state = _state(problems_dir)
    # В базе по-прежнему версия с Polygon — push загрузит локальную.
    assert state.synced["generators/gen_rand.cpp"] == pull_module.digest(b"// gen\n")
    assert "generators/gen_rand.cpp" in state.stamps


def test_overwrite_downloads_only_locally_changed_files(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    _write(problems_dir / PROBLEM_ID, "generators/gen_rand.cpp", "// local edit\n")
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["gen_rand.cpp"]
    assert _statuses(result)["generators/gen_rand.cpp"] == ITEM_OVERWRITTEN
    assert (problems_dir / PROBLEM_ID / "generators" / "gen_rand.cpp").read_bytes() == b"// gen\n"
    assert (result.backup_dir / "generators" / "gen_rand.cpp").read_text() == "// local edit\n"


def test_missing_local_file_is_downloaded_even_if_unchanged_on_polygon(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    _bump_revision(polygon)
    (problems_dir / PROBLEM_ID / "validator.cpp").unlink()
    polygon.calls.clear()

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["val.cpp"]
    assert _statuses(result)["validator.cpp"] == ITEM_DOWNLOADED


def test_unchanged_revision_skips_downloads_for_state_without_stamps(problems_dir, polygon):
    # Задача синхронизирована до появления отметок: ревизия та же — на
    # Polygon ничего не коммитили, хеши базы верны и без отметок.
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
        ["problem_lib.h", "val.cpp", "gen_rand.cpp", "ok_cpp_main_author.cpp", "spec.yaml"]
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
    """Polygon, у которого падает скачивание отдельных файлов по имени."""

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
    polygon.failing_names = {"ok_cpp_main_author.cpp"}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not result.ok
    state = _state(problems_dir)
    assert "solutions/ok_cpp_main_author.cpp" not in state.synced
    assert "solutions/ok_cpp_main_author.cpp" not in state.stamps
    assert state.revision is None
    _assert_stamps_paired(problems_dir)

    polygon.failing_names = set()
    polygon.calls.clear()
    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    # Ревизия базы неизвестна, решают отметки: скачивается только упавшее.
    assert result.ok
    assert polygon.downloads() == ["ok_cpp_main_author.cpp"]
    assert _state(problems_dir).revision == 5


def test_failed_listing_keeps_previous_pairs_valid(problems_dir, polygon):
    pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)
    before = _state(problems_dir)
    _bump_revision(polygon)
    polygon.failing = {"problem.solutions"}

    result = pull_problem(PROBLEM_ID, problems_dir=problems_dir, client=polygon)

    assert not result.ok
    after = _state(problems_dir)
    # Часть "solutions" не сверялась — её пара хеш+отметка от прошлого pull.
    key = "solutions/ok_cpp_main_author.cpp"
    assert after.synced[key] == before.synced[key]
    assert after.stamps[key] == before.stamps[key]
    assert after.revision == 5
    _assert_stamps_paired(problems_dir)

    # Решение поменяли на Polygon, пока листинг падал, — отметка не совпадёт.
    polygon.failing = set()
    polygon.solutions["ok_cpp_main_author.cpp"] = ("MA", b"// changed\n")
    polygon.mtimes["ok_cpp_main_author.cpp"] = 300
    polygon.calls.clear()
    result = pull_problem(PROBLEM_ID, overwrite=True, problems_dir=problems_dir, client=polygon)

    assert polygon.downloads() == ["ok_cpp_main_author.cpp"]
    assert _statuses(result)["solutions/ok_cpp_main_author.cpp"] == ITEM_OVERWRITTEN
