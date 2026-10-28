"""Прогон решений задачи на примерах — `orchestrator <задача> local run`.
Без модели и без сети; не судящий контур (CLAUDE.md, "Скоуп (текущий)"), а
проверка, что решения хотя бы на примерах ведут себя так, как заявляет
вердикт в их имени. Отдельно от `local check`: сборка и запуск всех решений
— долго, и нужны не при каждой проверке.

Что делается:

1. Тесты — `statement/examples/example_<N>.txt` (то, что уходит на Polygon
   примерами). Ответ N-го — `output` у `sample_examples[N-1]` спека; у
   примеров без него ответ — вывод Main correct (выбор — как у
   `upload_solutions`), и об этом печатается заметка.
2. Валидатор (`validator.cpp`, если есть) прогоняется на input каждого
   примера: пример, который он не пропускает, — ошибка.
3. Каждое решение из `solutions/` собирается один раз: C++ — g++ (как
   compile-check, стек процесса поднят до жёсткого лимита), Java — `javac`,
   а без него в PATH — тот же компилятор из модуля `jdk.compiler` через
   `java -m` (он есть везде, где `java` запускает `.java` без сборки), и
   запуск `java -Xmx<ML>m -Xss64m`; Python — `python3` без сборки. Вердикт
   на примере: TL — дольше TL задачи (`limits.yaml`, без него —
   `authoring/constraints.yaml`), RE — ненулевой код возврата, иначе —
   вердикт чекера (`checker.cpp` при `checker.custom_needed`, иначе
   стандартный чекер testlib из `checker.standard` — исходники в
   `std_checkers/`, тот же `testlib.h` из `templates/`).
4. Допустимое на примерах по вердикту из имени решения —
   `ALLOWED_VERDICTS`: решение с вердиктом X получает OK или X. Остальное —
   ошибка. Решение не с вердиктом `tl`, которое на примере работало дольше
   `SLOW_FRACTION` TL, — предупреждение: на больших тестах оно может не
   уложиться (у `tl_ok` — видно, насколько оно пограничное).

Время — wall-clock на локальной машине, с запуском JVM/интерпретатора:
компиляторы, флаги и скорость не те, что на Polygon, поэтому TL здесь —
сигнал, а не вердикт Polygon. ML не меряется.

Интерактивные задачи пока не поддерживаются: решения не запускаются (с
заметкой), валидатор — запускается.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from orchestrator.checks.compile_check import compile_check
from orchestrator.checks.run_with_timeout import RunResult, run_with_timeout
from orchestrator.checks.statement_check import STATEMENT_DIRNAME
from orchestrator.limits import LIMITS_FILENAME, LimitsError, parse_limits
from orchestrator.polygon.steps.base import PolygonStepError
from orchestrator.polygon.steps.upload_solutions import _choose_main, _parse_solution_filename
from orchestrator.polygon.sync import CHECKER_FILENAME, INTERACTOR_FILENAME, VALIDATOR_FILENAME
from orchestrator.problem_paths import (
    CONSTRAINTS_FILENAME,
    SPEC_FILENAME,
    find_authoring_file,
    is_removed_mark,
)

VERDICT_OK = "OK"
VERDICT_WA = "WA"
VERDICT_PE = "PE"
VERDICT_TL = "TL"
VERDICT_RE = "RE"
# Чекер упал или вернул неизвестный код — ошибка проверки, не решения.
VERDICT_FAIL = "FAIL"

# Вердикт из имени решения -> что допустимо на примерах. `ml`: память здесь
# не меряется, нехватка проявится как RE (OutOfMemoryError у Java). `il`
# (Idleness limit) бывает только в интерактивной задаче, которые пока не
# запускаются; простой без вывода в обычной задаче — TL.
ALLOWED_VERDICTS: dict[str, frozenset[str]] = {
    "ok": frozenset({VERDICT_OK}),
    "wa": frozenset({VERDICT_OK, VERDICT_WA}),
    "tl": frozenset({VERDICT_OK, VERDICT_TL}),
    "tle": frozenset({VERDICT_OK, VERDICT_TL}),
    "tl_ok": frozenset({VERDICT_OK, VERDICT_TL}),
    "re": frozenset({VERDICT_OK, VERDICT_RE}),
    "ml": frozenset({VERDICT_OK, VERDICT_RE}),
    "mle": frozenset({VERDICT_OK, VERDICT_RE}),
    "il": frozenset({VERDICT_OK, VERDICT_TL}),
}

# Доля TL, дольше которой решение не с вердиктом `tl` — предупреждение.
SLOW_FRACTION = 0.5
_TL_VERDICTS = frozenset({"tl", "tle"})

STD_CHECKERS_DIR = Path(__file__).with_name("std_checkers")
EXAMPLES_SUBDIR = "examples"
SOLUTIONS_DIRNAME = "solutions"

_EXAMPLE_NAME = re.compile(r"^example_(\d+)\.txt$")
_STD_PREFIX = "std::"
# Без `limits.yaml` и `constraints.yaml`.
_DEFAULT_TIME_LIMIT_MS = 2000
_DEFAULT_MEMORY_LIMIT_MB = 256
_CHECKER_TIMEOUT_SECONDS = 10.0
_COMPILE_TIMEOUT_SECONDS = 60.0
_JAVA_STACK = "-Xss64m"
_NO_JDK_HINT = "no Java compiler: install a JDK (sudo apt install default-jdk-headless)"

_PUBLIC_CLASS = re.compile(r"^\s*public\s+(?:final\s+|abstract\s+)*class\s+(\w+)", re.MULTILINE)
_ANY_CLASS = re.compile(r"^\s*(?:public\s+)?(?:final\s+|abstract\s+)*class\s+(\w+)", re.MULTILINE)


@dataclass(frozen=True)
class SampleTest:
    name: str
    input: str
    answer: str | None


@dataclass
class TestOutcome:
    test: str
    verdict: str
    seconds: float | None = None
    comment: str = ""


@dataclass
class SolutionOutcome:
    name: str
    verdict: str
    allowed: frozenset[str]
    tests: list[TestOutcome] = field(default_factory=list)
    # Не разобралось имя, не собралось, не запустилось.
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None or any(t.verdict not in self.allowed for t in self.tests)

    @property
    def runs(self) -> str:
        return ", ".join(
            t.verdict + (f" {t.seconds:.2f}s" if t.seconds is not None else "") for t in self.tests
        )


Progress = Callable[[str], None]


@dataclass
class SolutionsRunResult:
    # Что проверено и откуда взято (TL, чекер, ответы) — печатается по ходу.
    notes: list[str] = field(default_factory=list)
    # Проблемы не отдельного решения: валидатор, чекер, ответы.
    errors: list[str] = field(default_factory=list)
    solutions: list[SolutionOutcome] = field(default_factory=list)
    time_limit: float | None = None
    progress: Progress | None = None

    def note(self, message: str) -> None:
        self.notes.append(message)
        if self.progress is not None:
            self.progress(message)

    @property
    def failed(self) -> bool:
        return bool(self.errors) or any(s.failed for s in self.solutions)

    @property
    def warnings(self) -> list[str]:
        """Решения не с вердиктом `tl`, работавшие на примере дольше
        `SLOW_FRACTION` TL (но уложившиеся в него)."""
        if self.time_limit is None:
            return []
        threshold = SLOW_FRACTION * self.time_limit
        return [
            f"{s.name}: {t.test} {t.seconds:.2f}s — more than {SLOW_FRACTION:g} of TL "
            f"{self.time_limit:g}s"
            for s in self.solutions
            if s.verdict not in _TL_VERDICTS
            for t in s.tests
            if t.verdict != VERDICT_TL and t.seconds is not None and t.seconds > threshold
        ]

    def groups(self) -> list[tuple[str, list[str], str]]:
        """Итог по группам `(заголовок, строки, подсказка)` — как у `polygon
        push`; пустые группы печатающий пропускает."""
        expected = [f"{s.name}: {s.runs}" for s in self.solutions if not s.failed]
        unexpected = []
        not_run = []
        for s in self.solutions:
            if s.error is not None:
                first, *rest = s.error.strip().splitlines() or [""]
                not_run.append(f"{s.name}: {first}")
                not_run.extend(f"    {line}" for line in rest[:10])
            elif s.failed:
                unexpected.append(f"{s.name}: {s.runs}")
                for t in s.tests:
                    if t.verdict in s.allowed:
                        continue
                    allowed = ", ".join(sorted(s.allowed))
                    unexpected.append(f"  {t.test}: {t.verdict}, expected {allowed} for '{s.verdict}'")
                    unexpected.extend(f"    {line}" for line in t.comment.strip().splitlines()[-5:])
        return [
            ("expected verdicts", expected, ""),
            ("unexpected verdicts", unexpected, ""),
            ("not run", not_run, ""),
            ("warnings", self.warnings, ""),
            ("errors", self.errors, ""),
        ]


@dataclass
class _Program:
    command: list[str]
    unlimited_stack: bool = False

    def run(self, stdin: str, timeout_seconds: float) -> RunResult:
        return run_with_timeout(
            Path(self.command[0]),
            self.command[1:],
            timeout_seconds,
            stdin,
            unlimited_stack=self.unlimited_stack,
        )


class _BuildError(Exception):
    pass


def run_solutions(
    problem_dir: Path, templates_dir: Path, progress: Progress | None = None
) -> SolutionsRunResult:
    """Прогоняет валидатор и решения задачи на её примерах (см. докстринг
    модуля). Ничего в каталоге задачи не пишет. `progress` получает строки
    по ходу: заметки и запуск каждого решения."""
    problem_dir = Path(problem_dir)
    result = SolutionsRunResult(progress=progress)
    spec = _read_spec(problem_dir, result)
    tests = _sample_tests(problem_dir, spec)
    if not tests:
        result.note(f"no {STATEMENT_DIRNAME}/{EXAMPLES_SUBDIR}/example_<N>.txt — skipped")
        return result

    include_dirs = [problem_dir, Path(templates_dir)]
    with tempfile.TemporaryDirectory(prefix="solutions_run_") as tmp:
        work_dir = Path(tmp)
        _check_validator(problem_dir, include_dirs, tests, work_dir, result)

        if _is_interactive(problem_dir, spec):
            result.note("interactive problem: running solutions is not supported yet — skipped")
            return result

        sources = _solution_sources(problem_dir)
        if not sources:
            result.note(f"no {SOLUTIONS_DIRNAME}/ files — skipped")
            return result

        checker = _build_checker(problem_dir, spec, include_dirs, work_dir, result)
        if checker is None:
            return result
        time_limit_ms, memory_limit_mb = _limits(problem_dir, result)
        time_limit = time_limit_ms / 1000
        result.time_limit = time_limit

        parsed = {}
        for path in sources:
            try:
                parsed[path.name] = _parse_solution_filename(path)
            except PolygonStepError as exc:
                result.solutions.append(
                    SolutionOutcome(path.name, "?", frozenset(), error=str(exc))
                )

        programs: dict[str, _Program | str] = {}

        def program_for(path: Path) -> _Program | str:
            if path.name not in programs:
                try:
                    programs[path.name] = _build_solution(
                        path, include_dirs, work_dir, memory_limit_mb
                    )
                except _BuildError as exc:
                    programs[path.name] = str(exc)
            return programs[path.name]

        tests = _fill_answers(tests, parsed, program_for, time_limit, result)

        for name, solution in parsed.items():
            outcome = SolutionOutcome(
                name, solution.verdict, ALLOWED_VERDICTS.get(solution.verdict, frozenset())
            )
            result.solutions.append(outcome)
            if progress is not None:
                progress(f"running {name}")
            program = program_for(solution.path)
            if isinstance(program, str):
                outcome.error = program
                continue
            for test in tests:
                outcome.tests.append(
                    _run_test(program, checker, test, time_limit, work_dir)
                )
        _note_suspicious_answers(tests, result)
    result.solutions.sort(key=lambda s: s.name)
    return result


def _read_spec(problem_dir: Path, result: SolutionsRunResult) -> dict | None:
    """Спек как есть (YAML), без валидации: проверка нужна и задаче, спек
    которой ещё не проходит валидацию. Нет спека — `None`."""
    path = find_authoring_file(problem_dir, SPEC_FILENAME)
    if not path.is_file():
        result.note(f"no {SPEC_FILENAME}: no expected outputs and checker settings")
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        result.errors.append(f"{SPEC_FILENAME} is not valid YAML: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _section(spec: dict | None, name: str) -> dict:
    value = (spec or {}).get(name)
    return value if isinstance(value, dict) else {}


def _sample_tests(problem_dir: Path, spec: dict | None) -> list[SampleTest]:
    examples_dir = problem_dir / STATEMENT_DIRNAME / EXAMPLES_SUBDIR
    if not examples_dir.is_dir():
        return []
    spec_examples = _section(spec, "statement_draft").get("sample_examples") or []
    numbered = sorted(
        (int(match.group(1)), path)
        for path in examples_dir.iterdir()
        if path.is_file() and (match := _EXAMPLE_NAME.match(path.name))
    )
    tests = []
    for number, path in numbered:
        answer = None
        if 0 < number <= len(spec_examples) and isinstance(spec_examples[number - 1], dict):
            output = spec_examples[number - 1].get("output")
            if isinstance(output, (str, int, float)) and str(output).strip():
                answer = str(output)
        tests.append(SampleTest(path.stem, path.read_text(encoding="utf-8"), answer))
    return tests


def _is_interactive(problem_dir: Path, spec: dict | None) -> bool:
    if spec is not None:
        return spec.get("interactor") is not None
    return (problem_dir / INTERACTOR_FILENAME).is_file()


def _solution_sources(problem_dir: Path) -> list[Path]:
    solutions_dir = problem_dir / SOLUTIONS_DIRNAME
    if not solutions_dir.is_dir():
        return []
    return sorted(
        (p for p in solutions_dir.iterdir() if p.is_file() and not is_removed_mark(p)),
        key=lambda p: p.name,
    )


def _limits(problem_dir: Path, result: SolutionsRunResult) -> tuple[int, int]:
    for path in (problem_dir / LIMITS_FILENAME, find_authoring_file(problem_dir, CONSTRAINTS_FILENAME)):
        if not path.is_file():
            continue
        try:
            time_limit_ms, memory_limit_mb = parse_limits(path.read_text(encoding="utf-8"))
        except LimitsError as exc:
            result.errors.append(f"{path.name}: {exc}")
            continue
        result.note(
            f"TL {time_limit_ms / 1000:g}s, ML {memory_limit_mb} MB from {path.name} "
            "(local wall-clock time, not Polygon's)"
        )
        return time_limit_ms, memory_limit_mb
    result.note(
        f"no TL/ML in {LIMITS_FILENAME} or {CONSTRAINTS_FILENAME}: assuming "
        f"TL {_DEFAULT_TIME_LIMIT_MS / 1000:g}s, ML {_DEFAULT_MEMORY_LIMIT_MB} MB"
    )
    return _DEFAULT_TIME_LIMIT_MS, _DEFAULT_MEMORY_LIMIT_MB


def _compile_cpp(path: Path, include_dirs: list[Path], work_dir: Path) -> Path:
    compiled = compile_check(path, include_dirs, timeout_seconds=_COMPILE_TIMEOUT_SECONDS)
    if not compiled.success or compiled.binary_path is None:
        raise _BuildError(f"compilation failed:\n{compiled.stderr.strip()}")
    target = work_dir / f"{path.stem}.bin"
    shutil.move(str(compiled.binary_path), target)
    return target


def _check_validator(
    problem_dir: Path,
    include_dirs: list[Path],
    tests: list[SampleTest],
    work_dir: Path,
    result: SolutionsRunResult,
) -> None:
    path = problem_dir / VALIDATOR_FILENAME
    if not path.is_file():
        result.note(f"no {VALIDATOR_FILENAME}: examples are not validated")
        return
    if result.progress is not None:
        result.progress(f"running {VALIDATOR_FILENAME}")
    try:
        binary = _compile_cpp(path, include_dirs, work_dir)
    except _BuildError as exc:
        result.errors.append(f"{VALIDATOR_FILENAME}: {exc}")
        return
    rejected = 0
    for test in tests:
        run = run_with_timeout(binary, [], _CHECKER_TIMEOUT_SECONDS, test.input)
        if run.timed_out or run.exit_code != 0:
            rejected += 1
            reason = "timed out" if run.timed_out else (run.stderr or run.stdout).strip()
            result.errors.append(f"{VALIDATOR_FILENAME} rejects {test.name}: {reason}")
    if not rejected:
        result.note(f"{VALIDATOR_FILENAME} accepts all {len(tests)} example(s)")


def _build_checker(
    problem_dir: Path,
    spec: dict | None,
    include_dirs: list[Path],
    work_dir: Path,
    result: SolutionsRunResult,
) -> _Program | None:
    section = _section(spec, "checker")
    custom = bool(section.get("custom_needed")) if spec is not None else (
        problem_dir / CHECKER_FILENAME
    ).is_file()
    if custom:
        source = problem_dir / CHECKER_FILENAME
        label = CHECKER_FILENAME
        if not source.is_file():
            result.errors.append(
                f"checker.custom_needed is true, but there is no {CHECKER_FILENAME} — "
                "solutions are not run"
            )
            return None
    else:
        standard = str(section.get("standard") or "").strip()
        if not standard:
            result.errors.append(
                "checker.standard is not set in the spec — unknown checker, solutions are not run"
            )
            return None
        name = standard.removeprefix(_STD_PREFIX).removesuffix(".cpp")
        source = STD_CHECKERS_DIR / f"{name}.cpp"
        label = f"{_STD_PREFIX}{name}.cpp"
        if not source.is_file():
            known = ", ".join(sorted(p.stem for p in STD_CHECKERS_DIR.glob("*.cpp")))
            result.errors.append(
                f"unknown standard checker {standard!r} (known: {known}) — solutions are not run"
            )
            return None
    try:
        binary = _compile_cpp(source, include_dirs, work_dir)
    except _BuildError as exc:
        result.errors.append(f"checker {label}: {exc}")
        return None
    result.note(f"checker {label}")
    return _Program([str(binary)])


def _build_solution(
    path: Path, include_dirs: list[Path], work_dir: Path, memory_limit_mb: int
) -> _Program:
    suffix = path.suffix.lower()
    if suffix == ".cpp":
        return _Program([str(_compile_cpp(path, include_dirs, work_dir))], unlimited_stack=True)
    if suffix == ".java":
        return _build_java(path, work_dir, memory_limit_mb)
    if suffix == ".py":
        python = shutil.which("python3") or sys.executable
        return _Program([python, str(path)])
    raise _BuildError(f"unsupported language '{path.suffix}' — not run")


def _java_class_name(source: str) -> str | None:
    """Имя класса, под которым Polygon сохранит файл: public-класс, иначе
    первый класс файла."""
    match = _PUBLIC_CLASS.search(source) or _ANY_CLASS.search(source)
    return match.group(1) if match else None


def _javac_command() -> list[str] | None:
    javac = shutil.which("javac")
    if javac:
        return [javac]
    java = shutil.which("java")
    if java:
        return [java, "-m", "jdk.compiler/com.sun.tools.javac.Main"]
    return None


def _build_java(path: Path, work_dir: Path, memory_limit_mb: int) -> _Program:
    java = shutil.which("java")
    javac = _javac_command()
    if java is None or javac is None:
        raise _BuildError(_NO_JDK_HINT)
    source = path.read_text(encoding="utf-8")
    class_name = _java_class_name(source)
    if class_name is None:
        raise _BuildError("no class found in the Java source")
    # javac требует, чтобы public-класс лежал в файле со своим именем.
    src_dir = work_dir / f"java_{path.stem}"
    out_dir = src_dir / "out"
    out_dir.mkdir(parents=True)
    (src_dir / f"{class_name}.java").write_text(source, encoding="utf-8")
    compiled = run_with_timeout(
        Path(javac[0]),
        [*javac[1:], "-encoding", "UTF-8", "-d", str(out_dir), str(src_dir / f"{class_name}.java")],
        _COMPILE_TIMEOUT_SECONDS,
    )
    if compiled.timed_out:
        raise _BuildError(f"compilation exceeded timeout {_COMPILE_TIMEOUT_SECONDS}s")
    if compiled.exit_code != 0:
        if "jdk.compiler" in compiled.stderr and "not found" in compiled.stderr:
            raise _BuildError(_NO_JDK_HINT)
        raise _BuildError(f"compilation failed:\n{compiled.stderr.strip()}")
    return _Program(
        [java, f"-Xmx{memory_limit_mb}m", _JAVA_STACK, "-cp", str(out_dir), class_name]
    )


def _hard_timeout(time_limit: float) -> float:
    """Процесс убивается позже TL, чтобы было видно, насколько он не
    уложился."""
    return 2 * time_limit + 1


def _fill_answers(
    tests: list[SampleTest],
    parsed: dict,
    program_for,
    time_limit: float,
    result: SolutionsRunResult,
) -> list[SampleTest]:
    """Ответы примеров без `output` в спеке — вывод Main correct."""
    missing = [t for t in tests if t.answer is None]
    if not missing:
        result.note("expected outputs: from the spec")
        return tests
    names = ", ".join(t.name for t in missing)
    try:
        main_name = _choose_main(list(parsed.values()))
    except PolygonStepError:
        result.errors.append(f"no output in the spec for {names} and no ok solution to produce it")
        return tests
    program = program_for(parsed[main_name].path)
    result.note(f"expected outputs for {names}: from the main solution {main_name}")
    if isinstance(program, str):
        return tests
    filled = []
    for test in tests:
        if test.answer is None:
            run = program.run(test.input, _hard_timeout(time_limit))
            if not run.timed_out and run.exit_code == 0:
                test = SampleTest(test.name, test.input, run.stdout)
            else:
                result.errors.append(f"{test.name}: no expected output — {main_name} failed on it")
        filled.append(test)
    return filled


def _run_test(
    program: _Program,
    checker: _Program,
    test: SampleTest,
    time_limit: float,
    work_dir: Path,
) -> TestOutcome:
    run = program.run(test.input, _hard_timeout(time_limit))
    seconds = run.elapsed_seconds
    if run.timed_out or seconds > time_limit:
        return TestOutcome(test.name, VERDICT_TL, seconds)
    if run.exit_code != 0:
        return TestOutcome(
            test.name, VERDICT_RE, seconds, f"exit code {run.exit_code}\n{run.stderr}"
        )
    if test.answer is None:
        return TestOutcome(test.name, VERDICT_OK, seconds, "not judged: no expected output")
    judge_dir = work_dir / "judge"
    judge_dir.mkdir(exist_ok=True)
    files = {"input": test.input, "output": run.stdout, "answer": test.answer}
    for name, text in files.items():
        (judge_dir / f"{name}.txt").write_text(text, encoding="utf-8")
    # testlib-чекер: `checker <input> <output> <answer>`, комментарий — в stderr.
    judged = run_with_timeout(
        Path(checker.command[0]),
        [str(judge_dir / f"{name}.txt") for name in files],
        _CHECKER_TIMEOUT_SECONDS,
    )
    verdict = {0: VERDICT_OK, 1: VERDICT_WA, 2: VERDICT_PE}.get(judged.exit_code, VERDICT_FAIL)
    return TestOutcome(test.name, verdict, seconds, judged.stderr.strip())


def _note_suspicious_answers(tests: list[SampleTest], result: SolutionsRunResult) -> None:
    """Если на примере с ответом из спека WA у всех `ok`-решений — скорее
    ошибся ответ в спеке, чем все решения сразу."""
    ok_solutions = [s for s in result.solutions if s.verdict == "ok" and s.error is None]
    if len(ok_solutions) < 2:
        return
    for index, test in enumerate(tests):
        if all(
            index < len(s.tests) and s.tests[index].verdict == VERDICT_WA for s in ok_solutions
        ):
            result.errors.append(
                f"{test.name}: all ok solutions get WA — check the expected output in the spec"
            )
