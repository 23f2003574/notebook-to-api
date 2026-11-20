"""Suite-wide guards against state leaking between tests."""
import sys
import time

import pytest

_REAL_SLEEP = time.sleep


@pytest.fixture(autouse=True)
def _time_sleep_is_not_leaked():
    """A test that replaces time.sleep without restoring it makes every later
    time.sleep in the process a no-op, which silently broke the isolated-execution and
    task-cancellation tests that need a function to genuinely block."""
    yield
    if time.sleep is not _REAL_SLEEP:
        time.sleep = _REAL_SLEEP
        pytest.fail("test left time.sleep replaced; use monkeypatch.setattr")


@pytest.fixture(autouse=True)
def _dashboard_auth_failures_do_not_carry_over():
    """The dashboard counts failed API-key attempts per client address; every
    TestClient shares one address, so one test's wrong keys must not throttle
    the next test."""
    module = sys.modules.get("backend.dashboard")
    if module is not None:
        module._AUTH_FAILURES.clear()
    yield
    module = sys.modules.get("backend.dashboard")
    if module is not None:
        module._AUTH_FAILURES.clear()
