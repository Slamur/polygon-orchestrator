import shutil
from pathlib import Path

import pytest
import yaml

from orchestrator import cli
from orchestrator.checks.solutions_run import (
    ALLOWED_VERDICTS,
    _java_class_name,
    run_solutions,
)
from orchestrator.spec import _ALLOWED_VERDICTS

PROBLEM_ID = "example-problem"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Синтетическая задача: сумма двух чисел.
OK_PY = "a, b = map(int, input().split())\nprint(a + b)\n"
WA_PY = "a, b = map(int, input().split())\nprint(a - b)\n"
RE_PY = "raise SystemExit(3)\n"
SLOW_PY = "import time\ntime.sleep(1.5)\na, b = map(int, input().split())\nprint(a + b)\n"
OK_CPP = "#include <iostream>\nint main() { long long a, b; std::cin >> a >> b; std::cout << a + b << '\\n'; }\n"
OK_JAVA = (
    "import java.util.Scanner;\n"
    "public class Solution {\n"
    "    public static void main(String[] args) {\n"
    "        Scanner in = new Scanner(System.in);\n"
    "        System.out.println(in.nextLong() + in.nextLong());\n"
    "    }\n"
    "}\n"
)
VALIDATOR_CPP = (
    '#include "testlib.h"\n'
    "int main(int argc, char* argv[]) {\n"
    "    registerValidation(argc, argv);\n"
    '    inf.readInt(1, 100, "a"); inf.readSpace(); inf.readInt(1, 100, "b"); inf.readEoln();\n'
    "    inf.readEof();\n"
    "}\n"
)


def _problem(
    tmp_path: Path,
    solutions: dict[str, str],
    *,
    examples: list[tuple[str, str | None]] = (("1 2\n", "3\n"), ("10 20\n", "30\n")),
    checker: dict | None = None,
    time_limit: float = 1,
    extra_spec: dict | None = None,
) -> Path:
    problem_dir = tmp_path / "problems" / PROBLEM_ID
    examples_dir = problem_dir / "statement" / "examples"
    examples_dir.mkdir(parents=True)
    sample_examples = []
    for i, (test_input, output) in enumerate(examples, start=1):
        (examples_dir / f"example_{i}.txt").write_text(test_input, encoding="utf-8")
        sample_examples.append({"input": test_input, "output": output})
    spec = {
        "problem_id": PROBLEM_ID,
        "statement_draft": {"sample_examples": sample_examples},
        "checker": checker or {"standard": "ncmp", "custom_needed": False},
        **(extra_spec or {}),
    }
    (problem_dir / "authoring").mkdir()
    (problem_dir / "authoring" / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    (problem_dir / "limits.yaml").write_text(
        f"time_limit_seconds: {time_limit}\nmemory_limit_mb: 256\n", encoding="utf-8"
    )
    if solutions:
        (problem_dir / "solutions").mkdir()
    for name, text in solutions.items():
        (problem_dir / "solutions" / name).write_text(text, encoding="utf-8")
    return problem_dir


def _by_name(result):
    return {s.name: s for s in result.solutions}


def _group(result, title):
    return next(lines for group_title, lines, _ in result.groups() if group_title == title)


def test_allowed_verdicts_cover_every_spec_verdict():
    assert set(ALLOWED_VERDICTS) == _ALLOWED_VERDICTS
    assert all("OK" in allowed for allowed in ALLOWED_VERDICTS.values())


def test_verdicts_checked_against_solution_names(tmp_path):
    problem_dir = _problem(
        tmp_path,
        {
            "ok_cpp_main_draft.cpp": OK_CPP,
            "ok_py_wrong_draft.py": WA_PY,
            "wa_py_minus_draft.py": WA_PY,
            "wa_py_crash_draft.py": RE_PY,
            "re_py_crash_draft.py": RE_PY,
            "readme.txt": "not a solution\n",
        },
    )
    (problem_dir / "solutions" / "ok_py_old_draft.py.removed").write_text(WA_PY, encoding="utf-8")

    result = run_solutions(problem_dir, TEMPLATES_DIR)
    solutions = _by_name(result)

    assert "ok_py_old_draft.py.removed" not in solutions
    assert [t.verdict for t in solutions["ok_cpp_main_draft.cpp"].tests] == ["OK", "OK"]
    assert not solutions["ok_cpp_main_draft.cpp"].failed
    assert solutions["ok_py_wrong_draft.py"].failed
    assert not solutions["wa_py_minus_draft.py"].failed
    assert [t.verdict for t in solutions["wa_py_crash_draft.py"].tests] == ["RE", "RE"]
    assert solutions["wa_py_crash_draft.py"].failed
    assert not solutions["re_py_crash_draft.py"].failed
    assert solutions["readme.txt"].error
    assert result.failed
    assert "checker std::ncmp.cpp" in result.notes
    unexpected = _group(result, "unexpected verdicts")
    assert "  example_1: WA, expected OK for 'ok'" in unexpected
    assert "  example_1: RE, expected OK, WA for 'wa'" in unexpected
    assert any(line.startswith("readme.txt: ") for line in _group(result, "not run"))
    assert any(line.startswith("wa_py_minus_draft.py: ") for line in _group(result, "expected verdicts"))


def test_time_limit_from_limits_yaml(tmp_path):
    problem_dir = _problem(
        tmp_path,
        {"ok_py_slow_draft.py": SLOW_PY, "tl_py_slow_draft.py": SLOW_PY},
        examples=[("1 2\n", "3\n")],
        time_limit=0.5,
    )

    solutions = _by_name(run_solutions(problem_dir, TEMPLATES_DIR))

    assert [t.verdict for t in solutions["tl_py_slow_draft.py"].tests] == ["TL"]
    assert not solutions["tl_py_slow_draft.py"].failed
    assert solutions["ok_py_slow_draft.py"].failed


def test_missing_outputs_come_from_main_solution(tmp_path):
    problem_dir = _problem(
        tmp_path,
        {"ok_py_main_draft.py": OK_PY, "wa_py_minus_draft.py": WA_PY, "ok_py_wrong_draft.py": WA_PY},
        examples=[("1 2\n", None), ("10 20\n", "30\n")],
    )

    result = run_solutions(problem_dir, TEMPLATES_DIR)

    assert "expected outputs for example_1: from the main solution ok_py_main_draft.py" in result.notes
    assert [t.verdict for t in _by_name(result)["ok_py_wrong_draft.py"].tests] == ["WA", "WA"]


def test_wrong_output_in_spec_is_reported(tmp_path):
    problem_dir = _problem(
        tmp_path,
        {"ok_py_a_draft.py": OK_PY, "ok_py_b_draft.py": OK_PY},
        examples=[("1 2\n", "4\n"), ("10 20\n", "30\n")],
    )

    result = run_solutions(problem_dir, TEMPLATES_DIR)

    assert "example_1: all ok solutions get WA — check the expected output in the spec" in result.errors


def test_validator_rejecting_example(tmp_path):
    problem_dir = _problem(
        tmp_path, {"ok_py_main_draft.py": OK_PY}, examples=[("1 2\n", "3\n"), ("0 2\n", "2\n")]
    )
    (problem_dir / "validator.cpp").write_text(VALIDATOR_CPP, encoding="utf-8")

    result = run_solutions(problem_dir, TEMPLATES_DIR)

    rejected = [e for e in result.errors if e.startswith("validator.cpp rejects")]
    assert len(rejected) == 1 and "example_2" in rejected[0]


@pytest.mark.parametrize(
    "checker, message",
    [
        ({"standard": "nosuchcmp", "custom_needed": False}, "unknown standard checker 'nosuchcmp'"),
        ({"standard": None, "custom_needed": False}, "checker.standard is not set"),
        ({"standard": None, "custom_needed": True}, "there is no checker.cpp"),
    ],
)
def test_checker_problems_stop_solution_runs(tmp_path, checker, message):
    problem_dir = _problem(tmp_path, {"ok_py_main_draft.py": OK_PY}, checker=checker)

    result = run_solutions(problem_dir, TEMPLATES_DIR)

    assert result.solutions == []
    assert any(message in error for error in result.errors)


# Синтетический интерактив: интерактор сообщает n, решение отвечает 2n,
# интерактор подтверждает "ok", решение читает подтверждение и завершается.
INTERACTOR_CPP = (
    '#include "testlib.h"\n'
    "#include <iostream>\n"
    "int main(int argc, char* argv[]) {\n"
    "    registerInteraction(argc, argv);\n"
    "    int n = inf.readInt();\n"
    "    std::cout << n << std::endl;\n"
    '    int reply = ouf.readInt(-1000000, 1000000, "reply");\n'
    '    if (reply != 2 * n) quitf(_wa, "expected %d, got %d", 2 * n, reply);\n'
    '    std::cout << "ok" << std::endl;\n'
    "    tout << reply << std::endl;\n"
    '    quitf(_ok, "reply %d", reply);\n'
    "}\n"
)
INTERACTIVE_OK_PY = "import sys\nn = int(input())\nprint(2 * n, flush=True)\nsys.stdin.readline()\n"
INTERACTIVE_WA_PY = "import sys\nn = int(input())\nprint(n, flush=True)\nsys.stdin.readline()\n"
INTERACTIVE_NOFLUSH_PY = "import sys\nn = int(input())\nprint(2 * n)\nsys.stdin.readline()\n"
INTERACTIVE_RE_PY = "import sys\nn = int(input())\nprint(2 * n, flush=True)\nsys.stdin.readline()\nraise SystemExit(1)\n"


def _interactive_problem(tmp_path: Path, solutions: dict[str, str], *, interactor: bool = True) -> Path:
    problem_dir = _problem(
        tmp_path,
        solutions,
        # Реплики решения в диалоге, а не ответ: чекер их не видит.
        examples=[("5\n", "10\n\n")],
        checker={"standard": "wcmp", "custom_needed": False},
        extra_spec={"interactor": {"behavior_notes": "synthetic"}},
    )
    if interactor:
        (problem_dir / "interactor.cpp").write_text(INTERACTOR_CPP, encoding="utf-8")
    return problem_dir


def test_interactive_verdicts(tmp_path):
    problem_dir = _interactive_problem(
        tmp_path,
        {
            "ok_py_main_draft.py": INTERACTIVE_OK_PY,
            "wa_py_echo_draft.py": INTERACTIVE_WA_PY,
            "il_py_noflush_draft.py": INTERACTIVE_NOFLUSH_PY,
            "re_py_exit_draft.py": INTERACTIVE_RE_PY,
            "ok_py_wrong_draft.py": INTERACTIVE_WA_PY,
        },
    )

    result = run_solutions(problem_dir, TEMPLATES_DIR)
    verdicts = {s.name: [t.verdict for t in s.tests] for s in result.solutions}

    assert verdicts == {
        "il_py_noflush_draft.py": ["IL"],
        "ok_py_main_draft.py": ["OK"],
        "ok_py_wrong_draft.py": ["WA"],
        "re_py_exit_draft.py": ["RE"],
        "wa_py_echo_draft.py": ["WA"],
    }
    assert "expected outputs: tout of the main solution ok_py_main_draft.py" in result.notes
    assert [s.name for s in result.solutions if s.failed] == ["ok_py_wrong_draft.py"]
    assert "expected 10, got 5" in _by_name(result)["ok_py_wrong_draft.py"].tests[0].comment
    assert result.warnings == []


def test_interactive_problem_without_interactor(tmp_path):
    problem_dir = _interactive_problem(tmp_path, {"ok_py_main_draft.py": INTERACTIVE_OK_PY}, interactor=False)

    result = run_solutions(problem_dir, TEMPLATES_DIR)

    assert result.solutions == []
    assert any("without interactor.cpp" in error for error in result.errors)


def test_java_class_name():
    assert _java_class_name(OK_JAVA) == "Solution"
    assert _java_class_name("import x;\nclass Main {\n}\n") == "Main"
    assert _java_class_name("public final class Fast {}\n") == "Fast"
    assert _java_class_name("// no classes\n") is None


@pytest.mark.skipif(shutil.which("java") is None, reason="java is not installed")
def test_java_solution_compiled_under_its_class_name(tmp_path):
    problem_dir = _problem(tmp_path, {"ok_java_main_draft.java": OK_JAVA})

    solution = _by_name(run_solutions(problem_dir, TEMPLATES_DIR))["ok_java_main_draft.java"]

    assert solution.error is None
    assert [t.verdict for t in solution.tests] == ["OK", "OK"]


def test_slow_non_tl_solutions_are_warned(tmp_path):
    problem_dir = _problem(
        tmp_path,
        {"ok_py_slow_draft.py": SLOW_PY, "tl_ok_py_slow_draft.py": SLOW_PY, "tl_py_slow_draft.py": SLOW_PY},
        examples=[("1 2\n", "3\n")],
        time_limit=2,
    )

    result = run_solutions(problem_dir, TEMPLATES_DIR)

    assert not result.failed
    warned = sorted(line.split(":")[0] for line in result.warnings)
    assert warned == ["ok_py_slow_draft.py", "tl_ok_py_slow_draft.py"]
    assert "more than 0.5 of TL 2s" in result.warnings[0]


def test_local_run_prints_progress_and_groups(tmp_path, capsys):
    _problem(tmp_path, {"ok_py_main_draft.py": OK_PY, "ok_py_wrong_draft.py": WA_PY})

    rc = cli.main(["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "run"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "running ok_py_main_draft.py\nrunning ok_py_wrong_draft.py\n" in out
    assert out.index("=== expected verdicts ===") < out.index("=== unexpected verdicts ===")
    assert "\nok_py_wrong_draft.py: WA" in out.split("=== unexpected verdicts ===")[1]


def test_local_check_does_not_run_solutions(tmp_path, capsys):
    _problem(tmp_path, {"ok_py_wrong_draft.py": WA_PY})

    assert cli.main(["--base-dir", str(tmp_path), f"problems/{PROBLEM_ID}", "local", "check"]) == 0
    assert "running" not in capsys.readouterr().out
