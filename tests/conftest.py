"""Suite-wide guards against state leaking between tests."""
import os
import signal
import sys
import time

import pytest

# A suite started from a background job (`nohup pytest &`, some CI wrappers) inherits SIGINT as
# ignored, and every Python subprocess it spawns would then ignore Ctrl+C too: the CLI `--watch`
# tests, which interrupt a child with SIGINT, hung and failed only in that setup. Restore Python's
# normal handler so children start with the default.
if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
    signal.signal(signal.SIGINT, signal.default_int_handler)

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
    metrics = sys.modules.get("backend.auth_metrics")
    if metrics is not None:
        metrics.reset()
    health = sys.modules.get("backend.observability.deployment_governance_audit_health")
    if health is not None:
        health.audit_health.reset()
    yield
    module = sys.modules.get("backend.dashboard")
    if module is not None:
        module._AUTH_FAILURES.clear()


@pytest.fixture(autouse=True)
def _environment_is_not_leaked():
    """Fail a test that changes os.environ without restoring it, and restore it.

    A test that set NOTEBOOK_API_KEY directly once made 15 later tests answer 401;
    use monkeypatch.setenv/delenv (or a patch undone in a fixture) instead. Autouse
    fixtures are set up first and torn down last, so monkeypatch has already
    restored its own changes when this runs."""
    ignored = {"PYTEST_CURRENT_TEST"}  # pytest itself updates this around every test phase
    before = {k: v for k, v in os.environ.items() if k not in ignored}
    yield
    after = {k: v for k, v in os.environ.items() if k not in ignored}
    if after != before:
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
        current = os.environ.get("PYTEST_CURRENT_TEST")
        os.environ.clear()
        os.environ.update(before)
        if current is not None:
            os.environ["PYTEST_CURRENT_TEST"] = current
        pytest.fail("test left os.environ modified (use monkeypatch): " + ", ".join(changed))
