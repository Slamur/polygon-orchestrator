from pathlib import Path

from orchestrator.checks.source_charset import charset_error, is_source_file, non_ascii_lines


def test_is_source_file_by_suffix_and_script_name():
    assert is_source_file(Path("generators/gen_rand.cpp"))
    assert is_source_file(Path("solutions/ok.java"))
    assert is_source_file(Path("solutions/ok.py"))
    assert is_source_file(Path("test_script"))
    assert is_source_file(Path("test_script_groups"))
    assert not is_source_file(Path("statement/ru/legend.tex"))
    assert not is_source_file(Path("authoring/constraints.yaml"))


def test_non_ascii_lines_reports_line_numbers():
    text = "int main() {\n    // считаем ответ\n    return 0; // done\n}\n"
    assert non_ascii_lines(text) == [(2, "    // считаем ответ")]


def test_charset_error_lists_non_ascii_lines(tmp_path):
    source = tmp_path / "gen.cpp"
    source.write_text('// генератор\nint main() { ensuref(true, "ок"); }\n', encoding="utf-8")

    error = charset_error(source)

    assert error is not None
    assert "line 1: // генератор" in error
    assert 'line 2: int main() { ensuref(true, "ок"); }' in error


def test_charset_error_none_for_ascii_source_and_for_text_files(tmp_path):
    source = tmp_path / "gen.cpp"
    source.write_text("// generator\nint main() {}\n", encoding="utf-8")
    statement = tmp_path / "legend.tex"
    statement.write_text("Легенда задачи.\n", encoding="utf-8")

    assert charset_error(source) is None
    assert charset_error(statement) is None


REPO_ROOT = Path(__file__).parent.parent


def test_sample_sources_are_ascii():
    # The model extends the samples' style, comments included (testlib.h is upstream).
    sources = [
        path
        for root in (REPO_ROOT / "examples", REPO_ROOT / "templates")
        for path in root.rglob("*")
        if path.is_file() and is_source_file(path) and path.name != "testlib.h"
    ]
    assert sources
    assert {path.relative_to(REPO_ROOT).as_posix(): charset_error(path) for path in sources} == {
        path.relative_to(REPO_ROOT).as_posix(): None for path in sources
    }
