"""The platform's own dependencies are installed from a lock that pins every package with its hashes."""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ("requirements.lock", "requirements-dev.lock")
ENTRY = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")


def _entries(name):
    """{package: (version, [hashes])} of a lock file."""
    out, current = {}, None
    for line in (ROOT / name).read_text().splitlines():
        if ENTRY.match(line):
            current = ENTRY.match(line).group(1).lower().replace("_", "-")
            out[current] = (ENTRY.match(line).group(2), [])
        elif "--hash=sha256:" in line and current:
            out[current][1].append(re.search(r"sha256:([0-9a-f]{64})", line).group(1))
    return out


def _names(path):
    return {re.split(r"[=<>\[; ]", line.strip())[0].lower().replace("_", "-") for line in (ROOT / path).read_text().splitlines()
            if line.strip() and not line.startswith(("#", "-"))}


@pytest.mark.parametrize("name", LOCKS)
def test_every_entry_is_pinned_and_hashed(name):
    entries = _entries(name)
    assert entries
    assert [p for p, (_, hashes) in entries.items() if not hashes] == []
    text = (ROOT / name).read_text()
    assert not [l for l in text.splitlines() if l and not l.startswith((" ", "#", "--")) and not ENTRY.match(l)]


def test_the_lock_is_fresh_against_the_inputs_and_the_constraints():
    runtime, dev = _entries("requirements.lock"), _entries("requirements-dev.lock")
    assert _names("requirements.txt") <= set(runtime)
    assert _names("requirements-dev.txt") - {"r"} <= set(dev) | {"requirements.txt"}
    assert set(runtime) <= set(dev)  # the dev lock includes the runtime one
    for line in (ROOT / "constraints.txt").read_text().splitlines():
        match = ENTRY.match(line)
        if match:
            package = match.group(1).lower().replace("_", "-")
            for entries in (runtime, dev):
                if package in entries:
                    assert entries[package][0] == match.group(2), f"{package}: regenerate the lock (see README)"


def test_ci_and_the_dashboard_image_install_from_the_lock_with_hash_checking():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text()
    assert "-c constraints.txt" not in workflow
    assert workflow.count("--require-hashes -r requirements") >= 5
    image = (ROOT / "backend/generator/dashboard_deployment.py").read_text()
    assert "pip install --no-cache-dir --require-hashes -r requirements.lock" in image


def _wheel(directory):
    import base64
    import hashlib

    name = "lockdemo-1.0-py3-none-any.whl"
    files = {"lockdemo/__init__.py": b"VALUE = 1\n",
             "lockdemo-1.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: lockdemo\nVersion: 1.0\n",
             "lockdemo-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: t\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = "".join(f"{n},sha256={base64.urlsafe_b64encode(hashlib.sha256(d).digest()).rstrip(b'=').decode()},{len(d)}\n"
                     for n, d in files.items()) + "lockdemo-1.0.dist-info/RECORD,,\n"
    path = directory / name
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
        z.writestr("lockdemo-1.0.dist-info/RECORD", record)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install(tmp_path, requirement):
    lock = tmp_path / "t.lock"
    lock.write_text(requirement)
    return subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--no-index", "--find-links", str(tmp_path),
                           "--require-hashes", "-r", str(lock)], capture_output=True, text=True, timeout=120)


def test_pip_installs_the_right_hash_and_rejects_tampered_or_unlisted_packages(tmp_path):
    digest = _wheel(tmp_path)
    assert _install(tmp_path, f"lockdemo==1.0 \\\n    --hash=sha256:{digest}\n").returncode == 0
    tampered = _install(tmp_path, f"lockdemo==1.0 \\\n    --hash=sha256:{'0' * 64}\n")
    assert tampered.returncode != 0 and "HASH" in tampered.stderr.upper()
    unhashed = _install(tmp_path, "lockdemo==1.0\n")
    assert unhashed.returncode != 0 and "hash" in unhashed.stderr.lower()
