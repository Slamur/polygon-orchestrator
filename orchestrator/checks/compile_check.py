"""Compile-check for generated .cpp files (validator/checker/generator/solution).

This is not a full judging loop (see CLAUDE.md, the "Scope (current)" section) —
only a mechanical "compiles / doesn't compile" check plus the compiler's
stderr for diagnostics. The binary itself isn't run here.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_COMPILE_TIMEOUT_SECONDS = 30.0

COMPILE_FLAGS = ("-std=c++17", "-O2")


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
    """Compiles `path` with g++ with `-I` for each of `include_dirs` (in
    this search order), without running the result."""
    if isinstance(include_dirs, Path):
        include_dirs = [include_dirs]

    fd, tmp_binary = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".bin")
    os.close(fd)
    binary_path = Path(tmp_binary)

    try:
        result = subprocess.run(
            [
                "g++",
                *COMPILE_FLAGS,
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
