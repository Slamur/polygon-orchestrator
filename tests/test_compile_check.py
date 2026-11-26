from pathlib import Path

import pytest

from orchestrator.checks.compile_check import CompileResult, compile_check

# The module's tests build and run C++/Java, see the slow marker in pyproject.toml.
pytestmark = pytest.mark.slow

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cpp"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
TESTLIB_DIR = Path(__file__).parent.parent / "resources" / "testlib"


def test_compile_check_valid_cpp_succeeds():
    result = compile_check(FIXTURES_DIR / "valid_generator.cpp", [TEMPLATES_DIR, TESTLIB_DIR])

    assert isinstance(result, CompileResult)
    assert result.success is True
    assert result.binary_path is not None
    assert result.binary_path.exists()

    result.binary_path.unlink()


def test_compile_check_broken_cpp_fails():
    result = compile_check(FIXTURES_DIR / "broken_generator.cpp", [TEMPLATES_DIR, TESTLIB_DIR])

    assert result.success is False
    assert result.binary_path is None
    assert result.stderr != ""


def test_compile_check_searches_include_dirs_in_order(tmp_path):
    # the problem's own resource must be found before the default one
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    (problem_dir / "testlib.h").write_text(
        "#pragma once\ninline int only_in_problem_copy() { return 1; }\n"
    )
    source = tmp_path / "generators" / "gen.cpp"
    source.parent.mkdir()
    source.write_text('#include "testlib.h"\nint main() { return only_in_problem_copy() - 1; }\n')

    result = compile_check(source, [problem_dir, TESTLIB_DIR])
    assert result.success is True, result.stderr
    result.binary_path.unlink()

    assert compile_check(source, [TESTLIB_DIR]).success is False
