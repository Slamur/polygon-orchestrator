from orchestrator.resources import (
    STATEMENT_RESOURCES_DIR,
    STD_CHECKERS_DIR,
    TESTLIB_DIR,
    TESTLIB_FILENAME,
    default_resource_path,
    include_dirs,
    resource_path,
)


def test_default_resources_are_in_the_repository():
    assert (TESTLIB_DIR / TESTLIB_FILENAME).is_file()
    assert (STD_CHECKERS_DIR / "ncmp.cpp").is_file()


def test_problem_resources_come_before_the_defaults(tmp_path):
    assert include_dirs(tmp_path) == [tmp_path / "resources", TESTLIB_DIR]


def test_resource_path_takes_the_default_without_an_own_version(tmp_path):
    assert resource_path(tmp_path, TESTLIB_FILENAME) == TESTLIB_DIR / TESTLIB_FILENAME
    assert resource_path(tmp_path, "problem_lib.h") is None


def test_resource_path_takes_the_problem_version(tmp_path):
    own = tmp_path / "resources" / TESTLIB_FILENAME
    own.parent.mkdir()
    own.write_text("// the problem's own testlib\n")

    assert resource_path(tmp_path, TESTLIB_FILENAME) == own


def test_statement_files_are_defaults_but_not_include_dirs(tmp_path):
    for name in ("olymp.sty", "problem.tex", "statements.ftl"):
        assert default_resource_path(name) == STATEMENT_RESOURCES_DIR / name
    assert STATEMENT_RESOURCES_DIR not in include_dirs(tmp_path)
