from pathlib import Path

from orchestrator.checks.compile_check import CompileResult, compile_check

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cpp"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def test_compile_check_valid_cpp_succeeds():
    result = compile_check(FIXTURES_DIR / "valid_generator.cpp", TEMPLATES_DIR)

    assert isinstance(result, CompileResult)
    assert result.success is True
    assert result.binary_path is not None
    assert result.binary_path.exists()

    result.binary_path.unlink()


def test_compile_check_broken_cpp_fails():
    result = compile_check(FIXTURES_DIR / "broken_generator.cpp", TEMPLATES_DIR)

    assert result.success is False
    assert result.binary_path is None
    assert result.stderr != ""
