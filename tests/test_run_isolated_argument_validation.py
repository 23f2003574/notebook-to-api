import pytest

from backend.runtime.isolated_execution import run_isolated


@pytest.mark.parametrize("kw", [
    {"timeout": -1}, {"timeout": float("nan")}, {"memory_limit_mb": -5},
    {"cpu_limit_seconds": float("inf")}, {"memory_limit_mb": True}, {"cpu_limit_seconds": "3"},
])
def test_invalid_limits_are_rejected_before_a_child_is_started(kw):
    with pytest.raises(ValueError, match=next(iter(kw))):
        run_isolated(abs, (-1,), **kw)


def test_none_and_zero_limits_still_work():
    assert run_isolated(abs, (-3,), timeout=5, memory_limit_mb=None, cpu_limit_seconds=0) == 3
