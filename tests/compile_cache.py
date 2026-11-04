"""The `compile_check` cache for tests: the same sources with `testlib.h`
(a standard checker, an example's validator) are built in dozens of tests,
3–7 seconds each. The cache lives in `.pytest_cache` between pytest runs (in CI —
between runs via actions/cache); entries that haven't been read for a long time
are deleted by `prune`.

The key (`source_key`) is the preprocessor output (`g++ -E -P`) together with the build
flags and the g++ version: it has the contents of all included headers, but no
paths, so an identical source in different `tmp_path`s is built once.
A failed build isn't cached (stderr contains the source's path). Each call
gets its own copy of the binary — the calling code moves or deletes it."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import cache
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

from orchestrator.checks.compile_check import COMPILE_FLAGS, CompileResult


@cache
def _compiler_version() -> bytes:
    return subprocess.run(["g++", "--version"], capture_output=True, check=True).stdout


def source_key(path: Path, include_dirs: Sequence[Path]) -> str | None:
    """The build key of `path` with these `include_dirs`; None if the preprocessor
    failed — such a source isn't cached."""
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
        if key is None:
            return real(path, include_dirs, **kwargs)
        cached = cache_dir / f"{key}.bin"
        if not cached.is_file():
            # Under pytest-xdist the same source (a standard checker) is needed by
            # several processes at once: the first one builds it, the rest wait for its binary.
            with _locked(cache_dir / f"{key}.lock"):
                if not cached.is_file():
                    return _compile_into(real, cached, path, include_dirs, **kwargs)
        os.utime(cached)
        fd, copy = tempfile.mkstemp(prefix=f"{Path(path).stem}_", suffix=".bin")
        os.close(fd)
        shutil.copy2(cached, copy)
        return CompileResult(success=True, stderr="", binary_path=Path(copy))

    return compile_check


def _compile_into(
    real: Callable[..., CompileResult], cached: Path, path: Path, include_dirs, **kwargs
) -> CompileResult:
    result = real(path, include_dirs, **kwargs)
    if not result.success or result.binary_path is None:
        return result
    # Via a temporary file: a parallel pytest process mustn't
    # see a partially written binary.
    fd, partial = tempfile.mkstemp(dir=cached.parent, suffix=".partial")
    os.close(fd)
    shutil.copy2(result.binary_path, partial)
    os.replace(partial, cached)
    return result


@contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    """An interprocess lock for the duration of the build; without fcntl (Windows) — without it."""
    if fcntl is None:
        yield
        return
    with open(lock_path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def prune(cache_dir: Path, max_age_days: float) -> None:
    """Deletes from `cache_dir` the entries that weren't read or written for
    `max_age_days` days: sources change, and old binaries would pile up otherwise."""
    deadline = time.time() - max_age_days * 24 * 3600
    for entry in cache_dir.iterdir():
        try:
            if entry.stat().st_mtime < deadline:
                entry.unlink()
        except OSError:
            pass
