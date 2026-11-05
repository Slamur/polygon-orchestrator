"""A timeout wrapper around running a generated binary.

Like compile_check, this is a minimal safety net instead of a full judging
loop (see CLAUDE.md, the "Scope (current)" section): just the fact "fit in
time or not", the elapsed (wall-clock) and the CPU time. Distinguishing TLE/MLE and other
verdict analysis is out of scope. With `memory_cap_mb` the process's address
space is capped (`RLIMIT_AS`): an allocation over the cap fails, usually as a
runtime error. The peak memory isn't measured: `ru_maxrss` of a child carries over
the resident memory of the process it was forked from, i.e. of the orchestrator.
"""

from __future__ import annotations

import resource
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    elapsed_seconds: float = 0.0
    # The CPU time of the process: unlike the elapsed time, it doesn't include the
    # system crash handler (e.g. apport), which the kernel waits for after a crash.
    cpu_seconds: float = 0.0


def _children_cpu_seconds() -> float:
    # The sum over the reaped children: the delta around one run is that run's.
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def _raise_stack_limit() -> None:
    """The soft stack limit — up to the hard one: on Polygon the stack is large, and deep
    recursion in C++ mustn't fail just because of the default 8 MB."""
    _, hard = resource.getrlimit(resource.RLIMIT_STACK)
    resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))


def resource_limits(
    *, unlimited_stack: bool = False, memory_cap_mb: int | None = None
) -> Callable[[], None] | None:
    """`preexec_fn` for `subprocess`: the stack raised to the hard limit and/or
    the address space capped at `memory_cap_mb`; `None` — nothing to set."""
    if not unlimited_stack and memory_cap_mb is None:
        return None

    def apply() -> None:
        if unlimited_stack:
            _raise_stack_limit()
        if memory_cap_mb is not None:
            cap = memory_cap_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))

    return apply


def run_with_timeout(
    binary: Path,
    args: list[str],
    timeout_seconds: float,
    stdin_data: str | None = None,
    *,
    unlimited_stack: bool = False,
    memory_cap_mb: int | None = None,
) -> RunResult:
    """Runs `binary` with `args`, killing the process after `timeout_seconds`.
    `unlimited_stack` — raise the process's stack limit to the hard one;
    `memory_cap_mb` — cap its address space."""

    started = time.monotonic()
    cpu_before = _children_cpu_seconds()
    try:
        result = subprocess.run(
            [str(binary), *args],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            preexec_fn=resource_limits(
                unlimited_stack=unlimited_stack, memory_cap_mb=memory_cap_mb
            ),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout
            if isinstance(exc.stdout, str)
            else (exc.stdout or b"").decode(errors="replace")
        )
        stderr = (
            exc.stderr
            if isinstance(exc.stderr, str)
            else (exc.stderr or b"").decode(errors="replace")
        )
        return RunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=None,
            timed_out=True,
            elapsed_seconds=time.monotonic() - started,
            cpu_seconds=_children_cpu_seconds() - cpu_before,
        )

    return RunResult(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.returncode,
        timed_out=False,
        elapsed_seconds=time.monotonic() - started,
        cpu_seconds=_children_cpu_seconds() - cpu_before,
    )
