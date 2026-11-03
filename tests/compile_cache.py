"""Кэш `compile_check` на сессию тестов: одни и те же исходники с `testlib.h`
(стандартный чекер, валидатор примера) собираются в десятках тестов по
3–7 секунд каждый.

Ключ (`source_key`) — вывод препроцессора (`g++ -E -P`) вместе с флагами
сборки и версией g++: в нём содержимое всех включённых заголовков, но нет
путей, поэтому одинаковый исходник в разных `tmp_path` собирается один раз.
Неудачная сборка не кэшируется (stderr содержит путь исходника). Каждый вызов
получает свою копию бинаря — вызывающий код его переносит или удаляет."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from functools import cache
from pathlib import Path

from orchestrator.checks.compile_check import COMPILE_FLAGS, CompileResult


@cache
def _compiler_version() -> bytes:
    return subprocess.run(["g++", "--version"], capture_output=True, check=True).stdout


def source_key(path: Path, include_dirs: Sequence[Path]) -> str | None:
    """Ключ сборки `path` с этими `include_dirs`; None, если препроцессор не
    отработал — такой исходник не кэшируется."""
    try:
        result = subprocess.run(
            [
                "g++",
                *COMPILE_FLAGS,
                "-E",
                "-P",
                *(arg for directory in include_dirs for arg in ("-I", str(directory))),
                str(path),
            ],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    digest = hashlib.sha256()
    for part in (_compiler_version(), " ".join(COMPILE_FLAGS).encode(), result.stdout):
        digest.update(part)
        digest.update(b"\0")
    return digest.hexdigest()


def caching_compile_check(
    real: Callable[..., CompileResult], cache_dir: Path
) -> Callable[..., CompileResult]:
    def compile_check(path: Path, include_dirs: Path | Sequence[Path], **kwargs) -> CompileResult:
        dirs = [include_dirs] if isinstance(include_dirs, Path) else list(include_dirs)
        key = source_key(Path(path), dirs)
        cached = cache_dir / f"{key}.bin" if key else None
        if cached is None or not cached.is_file():
            result = real(path, include_dirs, **kwargs)
            if cached is None or not result.success or result.binary_path is None:
                return result
            shutil.copy2(result.binary_path, cached)
            return result
        fd, copy = tempfile.mkstemp(prefix=f"{Path(path).stem}_", suffix=".bin")
        os.close(fd)
        shutil.copy2(cached, copy)
        return CompileResult(success=True, stderr="", binary_path=Path(copy))

    return compile_check
