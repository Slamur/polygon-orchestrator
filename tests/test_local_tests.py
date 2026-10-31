import shutil
from pathlib import Path

import pytest

from orchestrator import cli
from orchestrator.checks.local_tests import (
    MANIFEST_FILENAME,
    TESTS_CACHE_DIRNAME,
    find_equal_tests,
    generate_tests,
    parse_chain,
    validate_tests,
)
from orchestrator.checks.test_script_check import ScriptCheckUnavailable, freemarker_jar

PROBLEM_ID = "example-problem"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Без testlib, чтобы тесты быстро собирались: печатает свои аргументы.
GEN_ARGS_CPP = (
    "#include <iostream>\n"
    "int main(int argc, char** argv) {\n"
    '    for (int i = 1; i < argc; ++i) std::cout << argv[i] << "\\n";\n'
    "}\n"
)
# Читает вход и дописывает к нему строку: звено цепочки.
UPD_APPEND_CPP = (
    "#include <iostream>\n"
    "#include <string>\n"
    "int main() {\n"
    "    std::string line;\n"
    '    while (std::getline(std::cin, line)) std::cout << line << "\\n";\n'
    '    std::cout << "appended\\n";\n'
    "}\n"
)
GEN_EMPTY_CPP = "int main() {}\n"
GEN_FAIL_CPP = "int main() { return 3; }\n"
# Пропускает всё, кроме входа, в котором есть строка `bad`.
VALIDATOR_CPP = (
    "#include <iostream>\n"
    "#include <string>\n"
    "int main() {\n"
    "    std::string line;\n"
    '    while (std::getline(std::cin, line)) if (line == "bad") return 1;\n'
    "}\n"
)


@pytest.fixture(autouse=True)
def _need_freemarker():
    if shutil.which("java") is None:
        pytest.skip("java not found")
    try:
        freemarker_jar()
    except ScriptCheckUnavailable as exc:
        pytest.skip(str(exc))


def _problem(
    tmp_path: Path,
    script: str,
    *,
    generators: dict[str, str] | None = None,
    examples: tuple[str, ...] = (),
    validator: str | None = None,
) -> Path:
    problem_dir = tmp_path / "problems" / PROBLEM_ID
    (problem_dir / "generators").mkdir(parents=True)
    (problem_dir / "test_script").write_text(script, encoding="utf-8")
    sources = {"gen_args": GEN_ARGS_CPP} if generators is None else generators
    for name, source in sources.items():
        (problem_dir / "generators" / f"{name}.cpp").write_text(source, encoding="utf-8")
    if examples:
        examples_dir = problem_dir / "statement" / "examples"
        examples_dir.mkdir(parents=True)
        for number, text in enumerate(examples, start=1):
            (examples_dir / f"example_{number}.txt").write_text(text, encoding="utf-8")
    if validator is not None:
        (problem_dir / "validator.cpp").write_text(validator, encoding="utf-8")
    return problem_dir


def _generate(problem_dir: Path):
    return generate_tests(problem_dir, TEMPLATES_DIR)


def test_parse_chain_splits_stages_and_normalizes_command():
    command, stages = parse_chain("gen -n=1  -k=2 | upd --desc > $")
    assert command == "gen -n=1 -k=2 | upd --desc"
    assert stages == [["gen", "-n=1", "-k=2"], ["upd", "--desc"]]


@pytest.mark.parametrize("line", ["gen -n=1", "gen -n=1 > 5", "gen | | upd > $"])
def test_parse_chain_rejects_lines_it_cannot_run(line):
    with pytest.raises(ValueError):
        parse_chain(line)


def test_tests_are_numbered_after_examples(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\ngen_args b > $\n", examples=("x\n", "y\n"))
    result = _generate(problem_dir)
    assert result.errors == []
    assert [t.number for t in result.tests] == [3, 4]
    assert [t.path.read_text() for t in result.tests] == ["a\n", "b\n"]
    manifest = (problem_dir / TESTS_CACHE_DIRNAME / MANIFEST_FILENAME).read_text()
    assert "3\t" in manifest and "gen_args a" in manifest


def test_chain_pipes_output_through_stages(tmp_path):
    problem_dir = _problem(
        tmp_path,
        "gen_args a | upd_append > $\n",
        generators={"gen_args": GEN_ARGS_CPP, "upd_append": UPD_APPEND_CPP},
    )
    result = _generate(problem_dir)
    assert result.errors == []
    assert result.tests[0].path.read_text() == "a\nappended\n"


def test_second_run_generates_nothing(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\ngen_args b > $\n")
    assert _generate(problem_dir).generated == 2
    second = _generate(problem_dir)
    assert (second.generated, second.reused, second.removed) == (0, 2, 0)


def test_inserting_a_line_renumbers_without_regenerating(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\ngen_args b > $\n")
    _generate(problem_dir)
    (problem_dir / "test_script").write_text("gen_args a > $\ngen_args new > $\ngen_args b > $\n")
    result = _generate(problem_dir)
    assert (result.generated, result.reused, result.removed) == (1, 2, 0)
    assert [t.path.read_text() for t in result.tests] == ["a\n", "new\n", "b\n"]


def test_removed_line_deletes_its_test(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\ngen_args b > $\n")
    gone = _generate(problem_dir).tests[1].path
    (problem_dir / "test_script").write_text("gen_args a > $\n")
    result = _generate(problem_dir)
    assert (result.generated, result.reused, result.removed) == (0, 1, 1)
    assert not gone.exists()


def test_generator_edit_regenerates_only_its_tests(tmp_path):
    # Скрипт не меняется — только код одного из генераторов.
    problem_dir = _problem(
        tmp_path,
        "gen_args a > $\ngen_other b > $\n",
        generators={"gen_args": GEN_ARGS_CPP, "gen_other": GEN_ARGS_CPP},
    )
    _generate(problem_dir)
    (problem_dir / "generators" / "gen_other.cpp").write_text(
        "// edited\n" + GEN_ARGS_CPP, encoding="utf-8"
    )
    result = _generate(problem_dir)
    assert (result.generated, result.reused, result.removed) == (1, 1, 1)


def test_problem_lib_edit_regenerates_everything(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\n")
    (problem_dir / "problem_lib.h").write_text("// one\n", encoding="utf-8")
    _generate(problem_dir)
    (problem_dir / "problem_lib.h").write_text("// two\n", encoding="utf-8")
    assert _generate(problem_dir).generated == 1


def test_relative_problem_dir_works(tmp_path, monkeypatch):
    # Генераторы запускаются из каталога кэша.
    _problem(tmp_path, "gen_args a > $\n")
    monkeypatch.chdir(tmp_path)
    result = generate_tests(Path("problems") / PROBLEM_ID, TEMPLATES_DIR)
    assert result.errors == []
    assert result.tests[0].path.read_text() == "a\n"


@pytest.mark.parametrize(
    ("script", "generators", "expected"),
    [
        ("gen_missing a > $\n", {}, "no generators/gen_missing.cpp"),
        ("gen_bad a > $\n", {"gen_bad": "not c++\n"}, "failed to compile"),
        ("gen_fail a > $\n", {"gen_fail": GEN_FAIL_CPP}, "exit code 3"),
        ("gen_empty a > $\n", {"gen_empty": GEN_EMPTY_CPP}, "empty output"),
        ("gen_args a > 5\n", {"gen_args": GEN_ARGS_CPP}, "only '> $' is supported"),
    ],
)
def test_bad_line_is_reported_and_others_are_generated(tmp_path, script, generators, expected):
    problem_dir = _problem(
        tmp_path, script + "gen_ok fine > $\n", generators={**generators, "gen_ok": GEN_ARGS_CPP}
    )
    result = _generate(problem_dir)
    assert len(result.errors) == 1
    assert expected in result.errors[0]
    assert [t.path.read_text() for t in result.tests] == ["fine\n"]


def test_script_with_identical_lines_is_not_generated(tmp_path):
    result = _generate(_problem(tmp_path, "gen_args a > $\ngen_args a > $\n"))
    assert result.tests == []
    assert "fix the script first" in result.errors[0]


def test_equal_tests_are_found_across_commands_and_examples(tmp_path):
    # Разные строки скрипта, одинаковое содержимое, в том числе с примером.
    problem_dir = _problem(
        tmp_path,
        "gen_args a > $\ngen_args b > $\ngen_other a > $\ngen_args x > $\n",
        generators={"gen_args": GEN_ARGS_CPP, "gen_other": GEN_ARGS_CPP},
        examples=("x\n",),
    )
    result = _generate(problem_dir)
    assert result.errors == []
    groups = [group.names for group in find_equal_tests(problem_dir, result.tests)]
    assert groups == [["example_1", "test 5"], ["test 2", "test 4"]]


def test_different_tests_are_not_equal(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\ngen_args b > $\n")
    assert find_equal_tests(problem_dir, _generate(problem_dir).tests) == []


def test_validator_rejects_reports_test_and_command(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args ok > $\ngen_args bad > $\n", validator=VALIDATOR_CPP)
    errors, validated = validate_tests(problem_dir, TEMPLATES_DIR, _generate(problem_dir).tests)
    assert validated
    assert len(errors) == 1
    assert "test 2" in errors[0] and "gen_args bad" in errors[0]


def test_no_validator_is_not_an_error(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\n")
    assert validate_tests(problem_dir, TEMPLATES_DIR, _generate(problem_dir).tests) == ([], False)


def _check(tmp_path: Path, *flags: str) -> int:
    return cli.main(
        ["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "check", *flags]
    )


def test_cli_full_passes_on_good_tests(tmp_path, capsys):
    _problem(tmp_path, "gen_args a > $\ngen_args b > $\n", validator=VALIDATOR_CPP)
    assert _check(tmp_path, "--full") == 0
    out = capsys.readouterr().out
    assert "tests: 2 (2 generated, 0 from cache, 0 removed)" in out
    assert "validator.cpp accepts all 2 test(s)" in out
    assert "no equal tests" in out


def test_cli_full_shows_equal_tests_with_script_lines_without_failing(tmp_path, capsys):
    _problem(
        tmp_path,
        "gen_args a > $\n\ngen_other a > $\ngen_args x > $\n",
        generators={"gen_args": GEN_ARGS_CPP, "gen_other": GEN_ARGS_CPP},
        examples=("x\n",),
    )
    assert _check(tmp_path, "--full") == 0
    out = capsys.readouterr().out
    assert "equal tests: 2 group(s)" in out
    assert "test 2 = test 3" in out
    assert "test 2: gen_args a (test_script line 1)" in out
    assert "test 3: gen_other a (test_script line 3)" in out
    assert "example_1 = test 4" in out
    assert "example_1: example of the statement" in out


def test_cli_full_fails_on_generator_error(tmp_path):
    _problem(tmp_path, "gen_fail a > $\n", generators={"gen_fail": GEN_FAIL_CPP})
    assert _check(tmp_path, "--full") == 1


def test_cli_without_full_generates_nothing(tmp_path):
    problem_dir = _problem(tmp_path, "gen_args a > $\n")
    assert _check(tmp_path) == 0
    assert not (problem_dir / TESTS_CACHE_DIRNAME).exists()


def test_cli_clear_removes_only_local_caches(tmp_path, capsys):
    problem_dir = _problem(tmp_path, "gen_args a > $\n")
    assert _check(tmp_path, "--full") == 0
    kept = [
        problem_dir / ".cache" / "statement_draft.json",
        problem_dir / "polygon_state.json",
        problem_dir / ".llm_backup" / "old.txt",
        problem_dir / ".fix_backup" / "old.txt",
        problem_dir / ".pull_backup" / "old.txt",
    ]
    for path in kept:
        path.parent.mkdir(exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    capsys.readouterr()

    args = ["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "clear"]
    assert cli.main(args) == 0

    assert "removed .tests_cache/" in capsys.readouterr().out
    assert not (problem_dir / TESTS_CACHE_DIRNAME).exists()
    assert all(path.exists() for path in kept)
    assert (problem_dir / "test_script").is_file()
    assert (problem_dir / "generators" / "gen_args.cpp").is_file()


def test_cli_clear_with_nothing_to_clear(tmp_path, capsys):
    _problem(tmp_path, "gen_args a > $\n")
    args = ["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "clear"]
    assert cli.main(args) == 0
    assert "nothing to clear" in capsys.readouterr().out


def test_cli_clear_regenerates_after_clearing(tmp_path, capsys):
    _problem(tmp_path, "gen_args a > $\n")
    _check(tmp_path, "--full")
    cli.main(["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "clear"])
    capsys.readouterr()
    assert _check(tmp_path, "--full") == 0
    assert "1 generated" in capsys.readouterr().out
