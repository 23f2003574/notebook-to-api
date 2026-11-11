import pytest

from backend.runtime import isolated_execution
from backend.runtime.execution_limits import isolation_platform_problem, validate_execution_limits

KW = dict(timeout_seconds=0, memory_limit_mb=0, cpu_limit_seconds=0, rlimits_supported=True)


def test_posix_platform_is_supported():
    assert isolation_platform_problem(["fork", "spawn", "forkserver"], True) is None


def test_this_platform_matches_its_own_capabilities():
    import multiprocessing
    import os

    expected_ok = "fork" in multiprocessing.get_all_start_methods() and hasattr(os, "killpg")
    assert (isolation_platform_problem() is None) == expected_ok


def test_windows_like_platform_is_reported_with_every_missing_facility():
    problem = isolation_platform_problem(["spawn"], False)

    assert '"fork"' in problem and "os.killpg" in problem


def test_missing_single_facility_names_only_that_facility():
    assert "killpg" not in isolation_platform_problem(["spawn"], True)
    assert "fork" not in isolation_platform_problem(["fork"], False)


def test_unsupported_platform_with_isolation_enabled_fails_early_and_actionably():
    problem = isolation_platform_problem(["spawn"], False)

    with pytest.raises(ValueError, match="NOTEBOOK_API_ISOLATE_EXECUTION=1 is not supported here.*Linux or macOS.*unset"):
        validate_execution_limits(isolate=True, isolation_problem=problem, **KW)


def test_unsupported_platform_with_isolation_disabled_is_untouched():
    validate_execution_limits(isolate=False, isolation_problem=isolation_platform_problem(["spawn"], False), **KW)


def test_supported_platform_and_unspecified_problem_preserve_behavior():
    validate_execution_limits(isolate=True, isolation_problem=None, **KW)
    validate_execution_limits(isolate=True, **KW)


def test_error_message_carries_no_environment_values(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_API_KEY", "super-secret-value")

    with pytest.raises(ValueError) as exc:
        validate_execution_limits(isolate=True, isolation_problem=isolation_platform_problem(["spawn"], False), **KW)

    assert "super-secret-value" not in str(exc.value)


def test_run_isolated_refuses_without_fork_instead_of_failing_obscurely(monkeypatch):
    monkeypatch.setattr(isolated_execution.mp, "get_all_start_methods", lambda: ["spawn"])

    with pytest.raises(isolated_execution.IsolatedExecutionError, match='no "fork"'):
        isolated_execution.run_isolated(lambda: 1)
