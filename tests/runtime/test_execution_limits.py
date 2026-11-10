import math

import pytest

from backend.runtime.execution_limits import validate_execution_limits


def _check(**overrides):
    args = dict(timeout_seconds=0, memory_limit_mb=0, cpu_limit_seconds=0, isolate=False, rlimits_supported=True)
    args.update(overrides)
    validate_execution_limits(**args)


def test_unset_defaults_are_valid_even_without_platform_support():
    _check(rlimits_supported=False)


def test_valid_limits_with_isolation():
    _check(timeout_seconds=30, memory_limit_mb=512, cpu_limit_seconds=5, isolate=True)


def test_wall_clock_timeout_works_without_isolation():
    _check(timeout_seconds=10)


@pytest.mark.parametrize("field", ["timeout_seconds", "memory_limit_mb", "cpu_limit_seconds"])
@pytest.mark.parametrize("bad", [-1, math.nan, math.inf, "5", None, True])
def test_malformed_or_negative_values_are_rejected(field, bad):
    with pytest.raises(ValueError, match="must be a finite number >= 0"):
        _check(isolate=True, **{field: bad})


def test_memory_limit_below_minimum_is_rejected():
    with pytest.raises(ValueError, match="at least 16"):
        _check(memory_limit_mb=1, isolate=True)


@pytest.mark.parametrize("field", ["memory_limit_mb", "cpu_limit_seconds"])
def test_limits_without_isolation_are_reported_not_ignored(field):
    with pytest.raises(ValueError, match="NOTEBOOK_API_ISOLATE_EXECUTION=1"):
        _check(**{field: 64})


@pytest.mark.parametrize("field", ["memory_limit_mb", "cpu_limit_seconds"])
def test_unsupported_platform_is_reported(field):
    with pytest.raises(ValueError, match="not supported on this platform"):
        _check(isolate=True, rlimits_supported=False, **{field: 64})
