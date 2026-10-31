"""Локальная генерация тестов по test-script'у — `orchestrator <задача> local
check --full`. Без модели и без сети; приближение к тому, что сгенерирует
Polygon, а не гарантия: `rnd` testlib детерминирован, но `long`,
`std::shuffle`, порядок `unordered_*` и нестабильная сортировка зависят от
компилятора и ОС.

Что делается:

1. Скрипт раскрывается и проверяется (`test_script_check.check_script`);
   скрипт с одинаковыми или непринимаемыми строками не генерируется.
2. Строка `<генератор> <параметры> [| <генератор> ...] > $` запускается
   цепочкой; вывод — тест. Номера тестов — после примеров: примеры занимают
   `testIndex` 1..N (`upload_statement`), `$` — следующий свободный.
   Явные номера (`> 5`) не поддерживаются: такие строки — ошибка.
3. Кэш — в `.tests_cache/` каталога задачи. Тест — чистая функция команды и
   кода генераторов: сид `registerGen` берётся из аргументов, а номер теста
   генератору не передаётся. Поэтому ключ теста (`_test_key`) — хеш команды
   и хешей сборки её генераторов (исходник, `problem_lib.h`, `testlib.h`,
   флаги); перенумерация тестов кэш не задевает, правка генератора
   инвалидирует только тесты с ним. Файлы с ключами, которых в наборе больше
   нет, удаляются. `manifest.txt` там же — номер, файл и команда каждого
   теста, для ручного разбора.
4. Одинаковые тесты ищутся по содержимому (`find_equal_tests`), вместе с
   примерами: разные команды могут дать один и тот же тест. Концы строк
   нормализуются, остальное сравнивается побайтово.
5. Валидатор (`validator.cpp`, если есть) прогоняется на каждом тесте.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.checks.compile_check import compile_check
from orchestrator.checks.run_with_timeout import _raise_stack_limit
from orchestrator.checks.statement_check import STATEMENT_DIRNAME
from orchestrator.checks.test_script_check import (
    ScriptCheckUnavailable,
    ScriptLine,
    ScriptRenderError,
    check_script,
)
from orchestrator.polygon.steps.upload_test_script import SCRIPT_FILENAMES
from orchestrator.polygon.sync import VALIDATOR_FILENAME
from orchestrator.problem_paths import is_removed_mark

TESTS_CACHE_DIRNAME = ".tests_cache"
MANIFEST_FILENAME = "manifest.txt"
GENERATORS_DIRNAME = "generators"
EXAMPLES_SUBDIR = "examples"
_EXAMPLE_NAME = re.compile(r"^example_(\d+)\.txt$")
_PROBLEM_LIB = "problem_lib.h"
_TESTLIB = "testlib.h"
# Меняются вместе с флагами `compile_check`: другие флаги — другая сборка.
_BUILD_VERSION = "g++ -std=c++17 -O2"
_COMPILE_TIMEOUT_SECONDS = 60.0
_GENERATOR_TIMEOUT_SECONDS = 60.0
_VALIDATOR_TIMEOUT_SECONDS = 30.0
_NEXT_TEST_TARGET = "$"

Progress = Callable[[str], None]


@dataclass(frozen=True)
class LocalTest:
    number: int
    command: str
    path: Path

    @property
    def name(self) -> str:
        return f"test {self.number}"


@dataclass
class EqualTests:
    """Группа тестов с одинаковым содержимым: имена в порядке номеров."""

    names: list[str]


@dataclass
class LocalTestsResult:
    tests: list[LocalTest] = field(default_factory=list)
    generated: int = 0
    reused: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _test_key(command: str, generator_hashes: list[str]) -> str:
    return _sha256("\n".join([command, *generator_hashes]).encode("utf-8"))


def parse_chain(text: str) -> tuple[str, list[list[str]]]:
    """Строка раскрытого скрипта -> (команда без `> $`, части цепочки: имя
    генератора и его аргументы). `ValueError` — строку нельзя запустить."""
    command, separator, target = text.rpartition(">")
    if not separator:
        raise ValueError("no '> $' redirect")
    if target.strip() != _NEXT_TEST_TARGET:
        raise ValueError(f"only '> $' is supported, got '> {target.strip()}'")
    stages = [stage.split() for stage in command.split("|")]
    if any(not stage for stage in stages):
        raise ValueError("empty command in the chain")
    return " ".join(command.split()), stages


def _generator_source(problem_dir: Path, name: str) -> Path | None:
    # На Polygon генератор называется по файлу без расширения.
    path = problem_dir / GENERATORS_DIRNAME / f"{name}.cpp"
    return path if path.is_file() and not is_removed_mark(path) else None


def _build_hash(source: Path, problem_dir: Path, templates_dir: Path) -> str:
    """Хеш всего, от чего зависит сборка: исходник и общие заголовки — той
    же версии, которую найдёт компилятор (`-I` задачи, потом `templates/`)."""
    digest = hashlib.sha256(_BUILD_VERSION.encode("utf-8"))
    digest.update(source.read_bytes())
    for header in (_PROBLEM_LIB, _TESTLIB):
        for directory in (problem_dir, templates_dir):
            path = directory / header
            if path.is_file():
                digest.update(path.read_bytes())
                break
    return digest.hexdigest()


def _build_generator(
    source: Path, build_hash: str, bin_dir: Path, problem_dir: Path, templates_dir: Path
) -> tuple[Path | None, str]:
    """(бинарь, ошибка). Сборка кэшируется по `build_hash`."""
    target = bin_dir / f"{source.stem}-{build_hash[:16]}.bin"
    if target.is_file():
        return target, ""
    compiled = compile_check(
        source, [problem_dir, templates_dir], timeout_seconds=_COMPILE_TIMEOUT_SECONDS
    )
    if not compiled.success or compiled.binary_path is None:
        return None, compiled.stderr.strip()
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(compiled.binary_path), target)
    return target, ""


def _run_chain(
    stages: list[tuple[Path, list[str]]], cwd: Path
) -> tuple[bytes | None, str]:
    """(вывод последнего генератора, ошибка): байты как есть, вывод каждого —
    вход следующего."""
    data = b""
    for binary, args in stages:
        try:
            run = subprocess.run(
                [str(binary), *args],
                input=data,
                capture_output=True,
                timeout=_GENERATOR_TIMEOUT_SECONDS,
                cwd=cwd,
                preexec_fn=_raise_stack_limit,
            )
        except subprocess.TimeoutExpired:
            return None, f"{binary.name} exceeded {_GENERATOR_TIMEOUT_SECONDS:.0f}s"
        if run.returncode != 0:
            stderr = run.stderr.decode("utf-8", "replace").strip()
            return None, f"{binary.name} exit code {run.returncode}: {stderr}"
        data = run.stdout
    return data, ""


def _describe_line(line: ScriptLine) -> str:
    where = f" (script line {line.source.line})" if line.source else ""
    return f'"{line.text}"{where}'


def generate_tests(
    problem_dir: Path, templates_dir: Path, progress: Progress | None = None
) -> LocalTestsResult:
    """Раскрывает скрипт задачи и приводит кэш тестов к текущему набору (см.
    докстринг модуля). Ошибка строки не мешает генерировать остальные."""
    # Генераторы запускаются из каталога кэша — пути должны быть абсолютными.
    problem_dir, templates_dir = Path(problem_dir).resolve(), Path(templates_dir).resolve()
    result = LocalTestsResult()
    # Первый существующий: так же выбирает скрипт `upload_test_script`.
    script = next(
        (problem_dir / name for name in SCRIPT_FILENAMES if (problem_dir / name).is_file()), None
    )
    if script is None:
        result.errors.append(f"no test script (expected one of: {', '.join(SCRIPT_FILENAMES)})")
        return result
    try:
        checked = check_script(script.read_text(encoding="utf-8"), script.name)
    except ScriptCheckUnavailable as exc:
        result.errors.append(f"{script.name}: check unavailable — {exc}")
        return result
    except ScriptRenderError as exc:
        result.errors.append(f"{script.name}: FreeMarker failed to expand the script:\n{exc}")
        return result
    if checked.failed:
        result.errors.append(f"{script.name}: tests are not generated — fix the script first:\n{checked.report}")
        return result

    cache_dir = problem_dir / TESTS_CACHE_DIRNAME
    objects_dir = cache_dir / "tests"
    bin_dir = cache_dir / "bin"
    objects_dir.mkdir(parents=True, exist_ok=True)

    builds: dict[str, tuple[str, Path | None, str]] = {}
    first_number = len(sample_paths(problem_dir)) + 1
    keep_keys: set[str] = set()
    keep_bins: set[str] = set()
    manifest: list[str] = []
    for index, line in enumerate(checked.lines):
        number = first_number + index
        try:
            command, stages = parse_chain(line.text)
        except ValueError as exc:
            result.errors.append(f"test {number}: {exc}: {_describe_line(line)}")
            continue

        chain: list[tuple[Path, list[str]]] = []
        hashes: list[str] = []
        failure = ""
        for name, *args in stages:
            if name not in builds:
                source = _generator_source(problem_dir, name)
                if source is None:
                    builds[name] = ("", None, f"no {GENERATORS_DIRNAME}/{name}.cpp")
                else:
                    build_hash = _build_hash(source, problem_dir, templates_dir)
                    if progress is not None and not (
                        bin_dir / f"{name}-{build_hash[:16]}.bin"
                    ).is_file():
                        progress(f"building {name}")
                    binary, error = _build_generator(
                        source, build_hash, bin_dir, problem_dir, templates_dir
                    )
                    builds[name] = (
                        build_hash,
                        binary,
                        f"{GENERATORS_DIRNAME}/{name}.cpp failed to compile:\n{error}" if error else "",
                    )
            build_hash, binary, error = builds[name]
            if binary is None:
                failure = error
                break
            keep_bins.add(binary.name)
            chain.append((binary, args))
            hashes.append(build_hash)
        if failure:
            result.errors.append(f"test {number}: {failure}")
            continue

        key = _test_key(command, hashes)
        path = objects_dir / key
        if path.is_file():
            result.reused += 1
        else:
            data, error = _run_chain(chain, cache_dir)
            if data is None:
                result.errors.append(f"test {number}: {error}: {_describe_line(line)}")
                continue
            if not data.strip():
                result.errors.append(f"test {number}: empty output: {_describe_line(line)}")
                continue
            part = path.with_name(key + ".part")
            part.write_bytes(data)
            part.replace(path)
            result.generated += 1
        keep_keys.add(key)
        result.tests.append(LocalTest(number, command, path))
        manifest.append(f"{number}\t{path.relative_to(cache_dir).as_posix()}\t{command}")

    for path in objects_dir.iterdir():
        if path.name not in keep_keys:
            path.unlink()
            result.removed += 1
    if bin_dir.is_dir():
        for path in bin_dir.iterdir():
            if path.name not in keep_bins:
                path.unlink()
    (cache_dir / MANIFEST_FILENAME).write_text(
        "".join(line + "\n" for line in manifest), encoding="utf-8"
    )
    return result


def example_files(examples_dir: Path) -> list[tuple[int, Path]]:
    """Файлы input примеров по порядку номеров: (N, `example_<N>.txt`)."""
    if not examples_dir.is_dir():
        return []
    return sorted(
        (int(match.group(1)), path)
        for path in examples_dir.iterdir()
        if path.is_file() and (match := _EXAMPLE_NAME.match(path.name))
    )


def sample_paths(problem_dir: Path) -> list[tuple[str, Path]]:
    """Примеры (тесты 1..N) по порядку: имя и файл input."""
    return [
        (path.stem, path)
        for _, path in example_files(problem_dir / STATEMENT_DIRNAME / EXAMPLES_SUBDIR)
    ]


def _normalized_hash(path: Path) -> str:
    return _sha256(path.read_bytes().replace(b"\r\n", b"\n"))


def find_equal_tests(problem_dir: Path, tests: list[LocalTest]) -> list[EqualTests]:
    """Группы тестов (и примеров) с одинаковым содержимым — в порядке первого
    появления."""
    groups: dict[str, list[str]] = {}
    for name, path in sample_paths(problem_dir):
        groups.setdefault(_normalized_hash(path), []).append(name)
    for test in tests:
        groups.setdefault(_normalized_hash(test.path), []).append(test.name)
    return [EqualTests(names) for names in groups.values() if len(names) > 1]


def validate_tests(
    problem_dir: Path, templates_dir: Path, tests: list[LocalTest]
) -> tuple[list[str], bool]:
    """(ошибки, запускался ли валидатор). Нет `validator.cpp` — не запускался.
    Сборка кэшируется, как у генераторов."""
    problem_dir, templates_dir = Path(problem_dir).resolve(), Path(templates_dir).resolve()
    source = problem_dir / VALIDATOR_FILENAME
    if not source.is_file():
        return [], False
    bin_dir = problem_dir / TESTS_CACHE_DIRNAME / "validator_bin"
    binary, error = _build_generator(
        source, _build_hash(source, problem_dir, templates_dir), bin_dir, problem_dir, templates_dir
    )
    if binary is None:
        return [f"{VALIDATOR_FILENAME}: compilation failed:\n{error}"], True
    for path in bin_dir.iterdir():
        if path != binary:
            path.unlink()
    errors = []
    for test in tests:
        try:
            with test.path.open("rb") as stdin:
                run = subprocess.run(
                    [str(binary)],
                    stdin=stdin,
                    capture_output=True,
                    timeout=_VALIDATOR_TIMEOUT_SECONDS,
                )
        except subprocess.TimeoutExpired:
            errors.append(f"{VALIDATOR_FILENAME} timed out on {test.name}: {test.command}")
            continue
        if run.returncode != 0:
            reason = (run.stderr or run.stdout).decode("utf-8", "replace").strip()
            errors.append(f"{VALIDATOR_FILENAME} rejects {test.name}: {reason}\n    {test.command}")
    return errors, True
