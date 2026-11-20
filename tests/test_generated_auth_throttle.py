"""A generated app throttles clients that keep sending wrong API keys."""
import importlib.util
import sys
import threading

import nbformat
import pytest
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook


def _load(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("NOTEBOOK_API_KEY", "right-key")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def ping(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp_path / "nb.ipynb")
    compile_notebook(str(tmp_path / "nb.ipynb"), str(tmp_path / "authapp"))
    sys.path.insert(0, str(tmp_path))
    spec = importlib.util.spec_from_file_location("authapp_app", tmp_path / "authapp" / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["authapp_app"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def load(tmp_path, monkeypatch):
    yield lambda **env: _load(tmp_path, monkeypatch, **env)
    sys.modules.pop("authapp_app", None)
    if str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def _post(client, key):
    headers = {"X-API-Key": key} if key else {}
    return client.post("/ping", json={"x": 1}, headers=headers)


def test_repeated_wrong_keys_are_throttled_with_retry_after(load):
    client = TestClient(load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="5").app)
    codes = [_post(client, f"bad{i}").status_code for i in range(8)]
    assert codes == [401] * 5 + [429] * 3
    blocked = _post(client, "bad9")
    assert blocked.status_code == 429 and 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert "bad9" not in blocked.text


def test_missing_keys_count_and_the_right_key_works_until_the_limit_is_hit(load):
    client = TestClient(load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="3").app)
    assert _post(client, "right-key").status_code == 200
    assert [_post(client, None).status_code for _ in range(3)] == [401, 401, 401]
    assert _post(client, None).status_code == 429
    assert _post(client, "right-key").status_code == 429  # documented: refused before the key is checked


def test_successful_requests_do_not_consume_the_failure_budget(load):
    client = TestClient(load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="2").app)
    assert [_post(client, "right-key").status_code for _ in range(10)] == [200] * 10
    assert _post(client, "bad").status_code == 401


def test_windows_expire_and_clients_are_independent(load, monkeypatch):
    module = load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="2")
    for _ in range(2):
        module._auth_throttle_record_failure("10.0.0.1")
    with pytest.raises(Exception):
        module._auth_throttle_check("10.0.0.1")
    module._auth_throttle_check("10.0.0.2")  # another client is unaffected
    now = module.time.time()
    monkeypatch.setattr(module.time, "time", lambda: now + 61)
    module._auth_throttle_check("10.0.0.1")  # window expired
    assert "10.0.0.1" not in module._AUTH_FAILURES


def test_tracking_state_is_bounded(load):
    module = load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="3")
    for i in range(module._AUTH_FAILURE_MAX_CLIENTS + 50):
        module._auth_throttle_record_failure(f"client-{i}")
    assert len(module._AUTH_FAILURES) <= module._AUTH_FAILURE_MAX_CLIENTS


def test_concurrent_failures_cannot_slip_past_the_limit(load):
    module = load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="50")
    threads = [threading.Thread(target=lambda: [module._auth_throttle_record_failure("c") for _ in range(25)]) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert module._AUTH_FAILURES["c"][1] == 200


def test_zero_disables_the_throttle_and_valid_per_key_rate_limiting_is_unchanged(load):
    client = TestClient(load(NOTEBOOK_API_AUTH_FAILURE_LIMIT_PER_MINUTE="0", NOTEBOOK_API_RATE_LIMIT_PER_MINUTE="3").app)
    assert {_post(client, f"bad{i}").status_code for i in range(20)} == {401}
    codes = [_post(client, "right-key").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
