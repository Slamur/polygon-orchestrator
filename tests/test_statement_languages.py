from pathlib import Path

import pytest

from orchestrator.polygon.state import (
    PolygonState,
    load_polygon_state,
    save_polygon_state,
)
from orchestrator.problem_paths import ProblemPathError
from orchestrator.statement_languages import (
    language_code,
    migrate_statement_languages,
    polygon_language,
    statement_languages,
)

PROBLEM_ID = "example-problem"


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _old_layout(tmp_path: Path) -> Path:
    problem_dir = tmp_path / PROBLEM_ID
    for name in ("legend.tex", "notes.tex", "name.txt", "tutorial.tex.removed"):
        _write(problem_dir / "statement" / name, name)
    _write(problem_dir / "statement" / "examples" / "example_1.txt", "1\n")
    return problem_dir


def test_codes_and_polygon_names():
    assert polygon_language("ru") == "russian"
    assert polygon_language("en") == "english"
    assert language_code("kazakh") == "kk"
    # A language without a code in the table — a directory with the Polygon name.
    assert polygon_language("latvian") == "latvian"
    assert language_code("latvian") == "latvian"


def test_migration_moves_statement_into_main_language(tmp_path):
    problem_dir = _old_layout(tmp_path)

    language, names = migrate_statement_languages(problem_dir)

    assert language == "ru"
    assert names == ["legend.tex", "name.txt", "notes.tex", "tutorial.tex.removed"]
    statement_dir = problem_dir / "statement"
    assert sorted(path.name for path in statement_dir.iterdir()) == ["examples", "ru"]
    assert (statement_dir / "ru" / "legend.tex").read_text() == "legend.tex"
    assert (statement_dir / "examples" / "example_1.txt").is_file()
    assert statement_languages(statement_dir) == ["ru"]
    # The second time there's nothing to move.
    assert migrate_statement_languages(problem_dir) is None


def test_migration_uses_spec_language(tmp_path):
    problem_dir = _old_layout(tmp_path)
    _write(problem_dir / "authoring" / "spec.yaml", "language: en\n")

    assert migrate_statement_languages(problem_dir)[0] == "en"
    assert (problem_dir / "statement" / "en" / "legend.tex").is_file()


def test_migration_renames_sync_keys(tmp_path):
    problem_dir = _old_layout(tmp_path)
    save_polygon_state(
        PROBLEM_ID,
        PolygonState(
            problem_id=PROBLEM_ID,
            polygon_id=1,
            created_at="2026-09-19T12:00:00+00:00",
            revision=3,
            synced={
                "statement/legend.tex": "a",
                "statement/name": "b",
                "statement/examples/example_1.txt": "c",
                "validator.cpp": "d",
            },
            stamps={"validator.cpp": "s"},
        ),
        problems_dir=tmp_path,
    )

    migrate_statement_languages(problem_dir)

    state = load_polygon_state(PROBLEM_ID, problems_dir=tmp_path)
    assert state.synced == {
        "statement/ru/legend.tex": "a",
        "statement/ru/name": "b",
        "statement/examples/example_1.txt": "c",
        "validator.cpp": "d",
    }
    assert state.stamps == {"validator.cpp": "s"}
    assert state.revision == 3


def test_migration_refuses_a_file_in_both_places(tmp_path):
    problem_dir = _old_layout(tmp_path)
    _write(problem_dir / "statement" / "ru" / "legend.tex", "new")

    with pytest.raises(ProblemPathError, match="legend.tex"):
        migrate_statement_languages(problem_dir)
    assert (problem_dir / "statement" / "notes.tex").is_file()


def test_nothing_to_migrate(tmp_path):
    assert migrate_statement_languages(tmp_path / "missing") is None
    _write(tmp_path / PROBLEM_ID / "statement" / "ru" / "legend.tex")
    assert migrate_statement_languages(tmp_path / PROBLEM_ID) is None


def test_language_directory_by_polygon_name_is_an_error(tmp_path):
    statement_dir = tmp_path / "statement"
    _write(statement_dir / "russian" / "legend.tex")

    with pytest.raises(ProblemPathError, match=r"\(ru for russian\)"):
        statement_languages(statement_dir)
