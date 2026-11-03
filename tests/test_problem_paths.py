"""Адрес задачи, вывод `problem_id` из пути и поиск каталогов задач
(`orchestrator/problem_paths.py`)."""

from pathlib import Path

import pytest

from orchestrator.problem_paths import (
    EXAMPLES_DIR,
    PROBLEMS_DIR,
    ProblemPathError,
    check_new_problem_location,
    migrate_to_authoring,
    parse_address_path,
    parse_problem_address,
    problem_address,
    problem_id_for,
    problem_paths,
    problems_under,
)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


@pytest.mark.parametrize(
    ("address", "root", "path"),
    [
        ("problems/my-task", PROBLEMS_DIR, "my-task"),
        ("problems/contest/2026/round-1/my-task/", PROBLEMS_DIR, "contest/2026/round-1/my-task"),
        ("examples/array/prefix-sums", EXAMPLES_DIR, "array/prefix-sums"),
    ],
)
def test_parse_problem_address_splits_root_and_path(address, root, path):
    assert parse_problem_address(address) == (root, path)


@pytest.mark.parametrize(
    ("address", "message"),
    [
        ("my-task", "must start with"),
        ("problems", "must start with"),
        ("problems/", "must start with"),
        ("other/my-task", "must start with"),
        ("problems/contest/-task", "not starting or ending with '-'"),
        ("problems/contest/my task", "must be ASCII"),
        ("examples/array--sums/x", "must not contain '--'"),
        ("problems/contest/2026", "must start with a letter"),
        ("examples/1-array/prefix-sums", "must start with a letter"),
    ],
)
def test_parse_problem_address_rejects_bad_addresses(address, message):
    with pytest.raises(ProblemPathError, match=message):
        parse_problem_address(address)


def test_problem_id_is_leaf_in_problems_and_joined_path_in_examples():
    assert problem_id_for(PROBLEMS_DIR, "contest/2026/round-1/my-task") == "my-task"
    assert problem_id_for(EXAMPLES_DIR, "array/prefix-sums") == "array--prefix-sums"
    # задача прямо в корне — одинаково для обоих корней
    assert problem_id_for(PROBLEMS_DIR, "my-task") == "my-task"
    assert problem_id_for(EXAMPLES_DIR, "my-task") == "my-task"


def test_problem_address_includes_root(tmp_path):
    assert (
        problem_address(tmp_path / "examples", "array/prefix-sums") == "examples/array/prefix-sums"
    )


def test_problem_paths_stop_at_problem_dirs_and_skip_hidden(tmp_path):
    root = tmp_path / "problems"
    _touch(root / "a" / "spec.yaml")
    _touch(root / "a" / "statement" / "spec.yaml")  # внутри задачи — не задача
    _touch(root / "g" / "b" / "polygon_state.json")
    _touch(root / "g" / "c" / "notes.txt")  # без спека и состояния — не задача
    _touch(root / ".hidden" / "spec.yaml")

    assert problem_paths(root) == ["a", "g/b"]
    assert problem_paths(tmp_path / "missing") == []


def test_new_problem_location_accepts_fresh_and_existing_same_problem(tmp_path):
    root = tmp_path / "problems"
    _touch(root / "contest" / "other" / "spec.yaml")

    check_new_problem_location(root, "contest/my-task")
    _touch(root / "contest" / "my-task" / "polygon_state.json")
    check_new_problem_location(root, "contest/my-task")


def test_new_problem_location_rejects_nesting(tmp_path):
    root = tmp_path / "problems"
    _touch(root / "outer" / "spec.yaml")
    _touch(root / "group" / "inner" / "spec.yaml")

    with pytest.raises(ProblemPathError, match="inside another problem problems/outer"):
        check_new_problem_location(root, "outer/my-task")
    with pytest.raises(ProblemPathError, match="already contains problem"):
        check_new_problem_location(root, "group")


def test_new_problem_location_rejects_duplicate_problem_id_in_any_root(tmp_path):
    _touch(tmp_path / "problems" / "2025" / "my-task" / "spec.yaml")
    _touch(tmp_path / "examples" / "array" / "prefix-sums" / "spec.yaml")

    with pytest.raises(ProblemPathError, match="already used by problems/2025/my-task"):
        check_new_problem_location(tmp_path / "problems", "2026/my-task")
    with pytest.raises(ProblemPathError, match="already used by examples/array/prefix-sums"):
        check_new_problem_location(tmp_path / "problems", "array--prefix-sums")
    with pytest.raises(ProblemPathError, match="already used by problems/2025/my-task"):
        check_new_problem_location(tmp_path / "examples", "my-task")


def test_problem_paths_find_problems_in_both_layouts_but_not_authoring_dir(tmp_path):
    _touch(tmp_path / "new-task" / "authoring" / "spec.yaml")
    _touch(tmp_path / "old-task" / "spec.yaml")
    _touch(tmp_path / "old-task" / "authoring" / "spec.yaml")

    assert problem_paths(tmp_path) == ["new-task", "old-task"]
    check_new_problem_location(tmp_path, "new-task")


def test_problem_address_rejects_authoring_component():
    with pytest.raises(ProblemPathError, match="authoring"):
        parse_problem_address("problems/my-task/authoring")


def test_migrate_to_authoring_moves_spec_constraints_and_draft(tmp_path):
    problem_dir = tmp_path / "my-task"
    for name in ("spec.yaml", "constraints.yaml", "draft.md", "limits.yaml", "validator.cpp"):
        _touch(problem_dir / name)

    moves = migrate_to_authoring(problem_dir)

    assert sorted(target.relative_to(problem_dir).as_posix() for _, target in moves) == [
        "authoring/constraints.yaml",
        "authoring/draft.md",
        "authoring/spec.yaml",
    ]
    assert sorted(path.name for path in problem_dir.iterdir()) == [
        "authoring",
        "limits.yaml",
        "validator.cpp",
    ]
    assert migrate_to_authoring(problem_dir) == []
    assert migrate_to_authoring(tmp_path / "missing") == []


def test_migrate_to_authoring_refuses_when_file_is_in_both_places(tmp_path):
    problem_dir = tmp_path / "my-task"
    _touch(problem_dir / "spec.yaml")
    _touch(problem_dir / "draft.md")
    _touch(problem_dir / "authoring" / "spec.yaml")

    with pytest.raises(ProblemPathError, match="spec.yaml"):
        migrate_to_authoring(problem_dir)
    assert (problem_dir / "draft.md").exists()


@pytest.mark.parametrize(
    ("address", "root", "path"),
    [
        ("problems", PROBLEMS_DIR, ""),
        ("examples/", EXAMPLES_DIR, ""),
        ("problems/contest/2026", PROBLEMS_DIR, "contest/2026"),
    ],
)
def test_parse_address_path_accepts_roots_and_group_names(address, root, path):
    assert parse_address_path(address) == (root, path)


def test_parse_address_path_rejects_unknown_root_and_authoring():
    with pytest.raises(ProblemPathError, match="must start with"):
        parse_address_path("other/my-task")
    with pytest.raises(ProblemPathError, match="authoring"):
        parse_address_path("problems/my-task/authoring")


def test_parse_problem_address_rejects_root_alone():
    with pytest.raises(ProblemPathError, match="must start with"):
        parse_problem_address("problems")


def test_problems_under_finds_problems_of_a_group(tmp_path):
    _touch(tmp_path / "contest" / "2026" / "task-a" / "authoring" / "spec.yaml")
    _touch(tmp_path / "contest" / "task-b" / "polygon_state.json")
    _touch(tmp_path / "other" / "task-c" / "authoring" / "spec.yaml")
    (tmp_path / "contest" / "empty").mkdir()

    assert problems_under(tmp_path, "contest") == ["contest/2026/task-a", "contest/task-b"]
    assert problems_under(tmp_path, "contest/2026") == ["contest/2026/task-a"]
    assert problems_under(tmp_path, "") == [
        "contest/2026/task-a",
        "contest/task-b",
        "other/task-c",
    ]


def test_problems_under_is_empty_for_a_problem_and_a_missing_or_empty_directory(tmp_path):
    _touch(tmp_path / "contest" / "task-a" / "authoring" / "spec.yaml")
    (tmp_path / "contest" / "empty").mkdir()

    assert problems_under(tmp_path, "contest/task-a") == []
    assert problems_under(tmp_path, "contest/empty") == []
    assert problems_under(tmp_path, "cont") == []
