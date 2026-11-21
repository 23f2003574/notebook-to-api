"""A hung test must be terminated and named, not left to hold the runner."""
import subprocess
import sys
import textwrap


def test_a_hung_test_is_terminated_with_a_useful_report(tmp_path):
    (tmp_path / "test_hang.py").write_text(textwrap.dedent("""
        import time

        def test_hangs_forever():
            time.sleep(600)
    """))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--timeout=2", "-q", str(tmp_path / "test_hang.py")],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "Timeout" in output and "test_hangs_forever" in output


def test_the_repository_configures_a_default_timeout():
    import configparser
    from pathlib import Path

    parser = configparser.ConfigParser()
    parser.read(Path(__file__).resolve().parent.parent / "pytest.ini")
    assert int(parser["pytest"]["timeout"]) >= 60
