"""compile --require-hashes: a complete hash-pinned requirements.txt installed with pip's hash checking."""
import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import nbformat
import pytest

from backend import compiler
from backend.compiler import VersionResolutionError, lock_requirements_with_hashes, require_hashes_in_dockerfile

ROOT = Path(__file__).resolve().parent.parent
DIGEST = "ab" * 32


def fake_resolver(requirements):
    return [("alpha", "1.0", DIGEST), ("beta", "2.0", "cd" * 32), ("beta", "2.0", "ef" * 32)]


def test_lock_lists_direct_and_transitive_dependencies_with_hashes(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("# generated\nalpha==1.0\n")
    assert lock_requirements_with_hashes(str(path), fake_resolver) == 2
    text = path.read_text()
    assert f"alpha==1.0 \\\n    --hash=sha256:{DIGEST}" in text
    assert text.count("--hash=sha256:") == 3 and "beta==2.0" in text  # transitive beta, two files


@pytest.mark.parametrize("content", ["alpha\n", "alpha>=1\n", "alpha==1.0\ngit+https://x/y.git\n"])
def test_unpinned_requirements_fail_closed_and_leave_the_file_alone(tmp_path, content):
    path = tmp_path / "requirements.txt"
    path.write_text(content)
    with pytest.raises(VersionResolutionError, match="exact version"):
        lock_requirements_with_hashes(str(path), fake_resolver)
    assert path.read_text() == content


def test_a_resolver_that_misses_a_dependency_fails_closed(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("gamma==3.0\n")
    with pytest.raises(VersionResolutionError, match="every requested"):
        lock_requirements_with_hashes(str(path), fake_resolver)
    assert path.read_text() == "gamma==3.0\n"


def test_dockerfile_installs_with_hash_checking(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-slim\nRUN pip install --no-cache-dir -r requirements.txt\n")
    require_hashes_in_dockerfile(str(dockerfile))
    assert "pip install --no-cache-dir --require-hashes -r requirements.txt" in dockerfile.read_text()
    dockerfile.write_text("FROM scratch\n")
    with pytest.raises(VersionResolutionError):
        require_hashes_in_dockerfile(str(dockerfile))


def test_pip_report_is_turned_into_digests_and_a_missing_digest_is_an_error(monkeypatch):
    def run_with(report):
        def fake_run(command, **kwargs):
            Path(command[command.index("--report") + 1]).write_text(json.dumps(report))
            return subprocess.CompletedProcess(command, 0, "", "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        return compiler.pip_hash_resolver(["alpha==1.0"])

    item = {"metadata": {"name": "Alpha_Pkg", "version": "1.0"}, "download_info": {"archive_info": {"hashes": {"sha256": DIGEST}}}}
    assert run_with({"install": [item]}) == [("alpha-pkg", "1.0", DIGEST)]
    legacy = {"metadata": {"name": "b", "version": "2"}, "download_info": {"archive_info": {"hash": f"sha256={DIGEST}"}}}
    assert run_with({"install": [legacy]}) == [("b", "2", DIGEST)]
    with pytest.raises(VersionResolutionError, match="no sha256"):
        run_with({"install": [{"metadata": {"name": "c", "version": "3"}, "download_info": {"url": "file:///c"}}]})


def _wheel(directory):
    name = "n2ademo-1.0-py3-none-any.whl"
    path = directory / name
    files = {
        "n2ademo/__init__.py": b"VALUE = 1\n",
        "n2ademo-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: n2ademo\nVersion: 1.0\n",
        "n2ademo-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = [f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b'=').decode()},{len(d)}" for n, d in files.items()]
    record.append("n2ademo-1.0.dist-info/RECORD,,")
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
        z.writestr("n2ademo-1.0.dist-info/RECORD", "\n".join(record) + "\n")
    return path


def _pip_install(tmp_path, requirements):
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "--require-hashes", "--no-index", "--find-links", str(tmp_path),
         "--target", str(tmp_path / "site"), "--disable-pip-version-check", "-r", str(requirements)],
        capture_output=True, text=True, timeout=120)


def test_pip_installs_the_lock_and_refuses_a_modified_hash(tmp_path):
    wheel = _wheel(tmp_path)
    real = hashlib.sha256(wheel.read_bytes()).hexdigest()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("n2ademo==1.0\n")
    lock_requirements_with_hashes(str(requirements), lambda r: [("n2ademo", "1.0", real)])
    assert _pip_install(tmp_path, requirements).returncode == 0
    assert (tmp_path / "site" / "n2ademo" / "__init__.py").exists()

    tampered = tmp_path / "tampered.txt"
    tampered.write_text(requirements.read_text().replace(real, "0" * 64))
    failed = _pip_install(tmp_path / ".", tampered)
    assert failed.returncode != 0 and "HASH" in failed.stderr.upper()


def test_a_requirements_file_without_hashes_fails_under_require_hashes(tmp_path):
    _wheel(tmp_path)
    requirements = tmp_path / "plain.txt"
    requirements.write_text("n2ademo==1.0\n")
    failed = _pip_install(tmp_path, requirements)
    assert failed.returncode != 0 and "hash" in failed.stderr.lower()


def test_compile_require_hashes_exits_1_when_dependencies_are_not_pinned(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("import not_a_real_library_xyz\ndef f(a: int) -> int:\n    return a\n")]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    result = subprocess.run(
        [sys.executable, "-m", "backend.cli", "compile", str(tmp_path / "nb.ipynb"), "--output", str(tmp_path / "out"),
         "--require-hashes"], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert result.returncode == 1 and "--require-hashes" in result.stdout
    assert "--require-hashes" not in (tmp_path / "out" / "Dockerfile").read_text()  # nothing half-hardened


def test_flag_is_documented_in_help():
    out = subprocess.run([sys.executable, "-m", "backend.cli", "compile", "--help"], cwd=ROOT, capture_output=True, text=True).stdout
    assert "--require-hashes" in out
