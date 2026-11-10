"""Validation of notebook execution resource-limit settings (no enforcement)."""
from __future__ import annotations

import math

MIN_MEMORY_LIMIT_MB = 16.0

_ISOLATE = "NOTEBOOK_API_ISOLATE_EXECUTION"
_MEMORY = "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB"
_CPU = "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS"
_TIMEOUT = "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS"


def validate_execution_limits(
    *,
    timeout_seconds: float,
    memory_limit_mb: float,
    cpu_limit_seconds: float,
    isolate: bool,
    rlimits_supported: bool,
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
