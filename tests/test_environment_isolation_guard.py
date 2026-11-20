"""The suite-wide environment guard (tests/conftest.py) catches leaks and restores the environment."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_inner(tmp_path, body):
    (tmp_path / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text())
    (tmp_path / "test_inner.py").write_text(textwrap.dedent(body))
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly", str(tmp_path / "test_inner.py")],
                          capture_output=True, text=True, cwd=tmp_path, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_an_intentional_leak_is_reported_and_does_not_reach_the_next_test(tmp_path):
    done = _run_inner(tmp_path, """
        import os
        def test_a_leaks():
            os.environ["GUARD_LEAK_PROBE"] = "1"
        def test_b_sees_a_clean_environment():
            assert "GUARD_LEAK_PROBE" not in os.environ
    """)
    assert "2 passed" in done.stdout and "1 error" in done.stdout, done.stdout
    assert "GUARD_LEAK_PROBE" in done.stdout and "use monkeypatch" in done.stdout


def test_removed_variables_are_detected_too(tmp_path):
    done = _run_inner(tmp_path, """
        import os
        def test_removes():
            os.environ.pop("PATH")
        def test_next():
            assert "PATH" in os.environ
    """)
    assert "PATH" in done.stdout and "2 passed" in done.stdout and "1 error" in done.stdout, done.stdout


def test_monkeypatch_and_fixture_setup_are_not_flagged(tmp_path):
    done = _run_inner(tmp_path, """
        import os, pytest
        @pytest.fixture
        def key(monkeypatch):
            monkeypatch.setenv("GUARD_OK_PROBE", "1")
        def test_uses_monkeypatch(monkeypatch):
            monkeypatch.setenv("GUARD_OK_PROBE_2", "1")
            monkeypatch.delenv("PATH")
        def test_uses_fixture(key):
            assert os.environ["GUARD_OK_PROBE"] == "1"
        def test_after():
            assert "GUARD_OK_PROBE" not in os.environ and "PATH" in os.environ
    """)
    assert "3 passed" in done.stdout, done.stdout


def test_restoration_happens_even_when_the_leaking_test_fails(tmp_path):
    done = _run_inner(tmp_path, """
        import os
        def test_leaks_then_fails():
            os.environ["GUARD_FAIL_PROBE"] = "1"
            assert False
        def test_next():
            assert "GUARD_FAIL_PROBE" not in os.environ
    """)
    assert "1 passed" in done.stdout and "1 failed" in done.stdout and "GUARD_FAIL_PROBE" in done.stdout, done.stdout
