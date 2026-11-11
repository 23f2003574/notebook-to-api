"""Validation of notebook execution resource-limit settings (no enforcement)."""
from __future__ import annotations

import math

MIN_MEMORY_LIMIT_MB = 16.0

_ISOLATE = "NOTEBOOK_API_ISOLATE_EXECUTION"
_MEMORY = "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB"
_CPU = "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS"
_TIMEOUT = "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS"
_MAX_PROCS = "NOTEBOOK_API_ISOLATE_MAX_PROCESSES"


def isolation_platform_problem(start_methods=None, has_killpg=None):
    """Why process isolation cannot run here, or None when it can.

    Isolation needs the "fork" start method (the call is a closure that
    "spawn" cannot pickle) and process groups (os.killpg) so a timeout also
    kills subprocesses the notebook started. In practice: Linux and macOS;
    not Windows. The arguments exist so tests can model other platforms.
    """
    import multiprocessing
    import os

    if start_methods is None:
        start_methods = multiprocessing.get_all_start_methods()
    if has_killpg is None:
        has_killpg = hasattr(os, "killpg")
    missing = []
    if "fork" not in start_methods:
        missing.append('the "fork" process start method')
    if not has_killpg:
        missing.append("process groups (os.killpg)")
    if missing:
        return "this platform lacks " + " and ".join(missing)
    return None


def validate_execution_limits(
    *,
    timeout_seconds: float,
    memory_limit_mb: float,
    cpu_limit_seconds: float,
    isolate: bool,
    rlimits_supported: bool,
    max_processes: int = 0,
    isolation_problem: str | None = None,
) -> None:
    """Raise ValueError with an actionable message for an unusable configuration.

    ``0`` means "disabled" for every limit (the documented default). Malformed,
    negative or non-finite values are rejected here as well, so the check holds
    for any caller. Memory/CPU limits are enforced only inside isolated child
    processes on platforms with ``resource``; setting one where it cannot take
    effect is an error rather than a silently ignored setting.
    """
    for name, value in ((_TIMEOUT, timeout_seconds), (_MEMORY, memory_limit_mb), (_CPU, cpu_limit_seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite number >= 0 (0 disables it), got {value!r}")
    if isinstance(max_processes, bool) or not isinstance(max_processes, int) or max_processes < 0:
        raise ValueError(f"{_MAX_PROCS} must be a whole number >= 0 (0 disables it), got {max_processes!r}")
    if isolate and isolation_problem:
        raise ValueError(
            f"{_ISOLATE}=1 is not supported here: {isolation_problem}. Run on Linux or macOS "
            f"(or in the generated Docker image), or unset {_ISOLATE}"
        )
    if max_processes > 0 and not isolate:
        raise ValueError(f"{_MAX_PROCS} is set but has no effect unless {_ISOLATE}=1; enable isolation or unset it")
    if 0 < memory_limit_mb < MIN_MEMORY_LIMIT_MB:
        raise ValueError(
            f"{_MEMORY} must be at least {MIN_MEMORY_LIMIT_MB:g} MB (the interpreter cannot run below that), "
            f"got {memory_limit_mb:g}; use 0 to disable"
        )
    for name, value in ((_MEMORY, memory_limit_mb), (_CPU, cpu_limit_seconds)):
        if value > 0 and not isolate:
            raise ValueError(f"{name} is set but has no effect unless {_ISOLATE}=1; enable isolation or unset {name}")
        if value > 0 and not rlimits_supported:
            raise ValueError(f"{name} is not supported on this platform (no resource.setrlimit); unset it")
