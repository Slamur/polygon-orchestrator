"""Compile-check для сгенерированных .cpp файлов (валидатор/чекер/генератор/решение).

Это не полноценный судящий контур (см. CLAUDE.md, раздел "Скоуп (текущий)") —
только механическая проверка "компилируется / не компилируется" плюс stderr
компилятора для диагностики. Сам бинарь здесь не запускается.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_DEFAULT_COMPILE_TIMEOUT_SECONDS = 30.0


@dataclass
class CompileResult:
    success: bool
    stderr: str
    binary_path: Path | None = None


def compile_check(
    path: Path,
    include_dirs: Path | Sequence[Path],
    *,
    timeout_seconds: float = _DEFAULT_COMPILE_TIMEOUT_SECONDS,
) -> CompileResult:
    """Компилирует `path` через g++ с `-I` на каждый из `include_dirs` (в
    этом порядке поиска), не запуская результат."""
    if isinstance(include_dirs, Path):
        include_dirs = [include_dirs]

    fd, tmp_binary = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".bin")
    os.close(fd)
    binary_path = Path(tmp_binary)

    try:
        result = subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O2",
                *(arg for directory in include_dirs for arg in ("-I", str(directory))),
                str(path),
                "-o",
                str(binary_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        binary_path.unlink(missing_ok=True)
        return CompileResult(
            success=False,
            stderr=f"compilation exceeded timeout {timeout_seconds}s: {exc}",
        )

    if result.returncode != 0:
        binary_path.unlink(missing_ok=True)
        return CompileResult(success=False, stderr=result.stderr)

    return CompileResult(success=True, stderr=result.stderr, binary_path=binary_path)
