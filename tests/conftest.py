"""Suite-wide guards against state leaking between tests."""
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
