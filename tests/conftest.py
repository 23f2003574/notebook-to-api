"""Suite-wide guards against state leaking between tests."""
import os
import tempfile

# Under pytest-xdist every worker runs in its own working directory. The dashboard modules default to
# ./uploads, ./generated and ./data (relative to the working directory), so without this tests on
# different workers would list and delete each other's files. The worker directory holds real
# uploads/ (seeded with the tracked sample) and generated/ directories and symlinks to everything
# else in the repository, so relative paths such as notebooks/sample.ipynb keep working.
if os.environ.get("PYTEST_XDIST_WORKER"):
    import shutil

    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _work = os.path.join(tempfile.gettempdir(), f"n2a-tests-{os.getpid()}-{os.environ['PYTEST_XDIST_WORKER']}")
    os.makedirs(_work, exist_ok=True)
    for _entry in os.listdir(_repo):
        if _entry in {".git", "uploads", "generated", "data"} or os.path.lexists(os.path.join(_work, _entry)):
            continue
        os.symlink(os.path.join(_repo, _entry), os.path.join(_work, _entry))
    os.makedirs(os.path.join(_work, "uploads"), exist_ok=True)
    os.makedirs(os.path.join(_work, "generated"), exist_ok=True)
    if os.path.isfile(os.path.join(_repo, "uploads", "sample.ipynb")):
        shutil.copy(os.path.join(_repo, "uploads", "sample.ipynb"), os.path.join(_work, "uploads"))
    os.chdir(_work)
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


# Files whose tests bind fixed ports, start servers or containers, or change the process-wide
# working directory. Under `pytest -n auto --dist loadgroup` (the CI invocation) they all run
# one after another on a single worker; every other test file is distributed freely.
SERIAL_TEST_FILES = frozenset({
    "test_cli.py", "test_cli_remote_audit.py", "test_compose_runtime_smoke.py", "test_serve.py",
    "test_dashboard_container.py", "test_upload_routes.py", "test_load_balancer.py",
})


def pytest_collection_modifyitems(config, items):
    import pytest

    for item in items:
        if item.path.name in SERIAL_TEST_FILES:
            item.add_marker(pytest.mark.xdist_group("serial"))


# Files whose tests depend on running in order in one process (a module-scoped dashboard they share and
# tamper with). They are left out of a parallel run -- whatever options reach the xdist workers -- and
# CI runs them afterwards with a plain `pytest tests/test_cli_remote_audit.py`.
NON_PARALLEL_TEST_FILES = frozenset({"test_cli_remote_audit.py"})


def pytest_ignore_collect(collection_path, config):
    parallel = bool(os.environ.get("PYTEST_XDIST_WORKER")) or bool(getattr(config.option, "numprocesses", None))
    if parallel and collection_path.name in NON_PARALLEL_TEST_FILES:
        return True
    return None
