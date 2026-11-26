"""CI runs third-party code with the repository's token: pin it and verify what it downloads."""
import hashlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.y*ml"))


def _steps():
    for path in WORKFLOWS:
        for job_name, job in (yaml.safe_load(path.read_text()).get("jobs") or {}).items():
            for step in job.get("steps", []):
                yield path.name, job_name, step


def test_every_third_party_action_is_pinned_to_a_full_commit_sha():
    unpinned = [
        f"{file}:{job}: {step['uses']}"
        for file, job, step in _steps()
        if "uses" in step and not step["uses"].startswith("./")
        and not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"])
    ]
    assert not unpinned, unpinned


def test_pinned_actions_keep_a_readable_version_comment():
    for path in WORKFLOWS:
        for line in path.read_text().splitlines():
            if re.search(r"uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}", line):
                assert re.search(r"#\s*v\d", line), line


def test_remote_downloads_are_never_piped_into_an_interpreter_or_extractor():
    for file, job, step in _steps():
        script = step.get("run", "")
        for line in script.splitlines():
            if re.search(r"\b(curl|wget)\b.*https?://(?!localhost|127\.0\.0\.1)", line):
                assert not re.search(r"\|\s*(tar|sh|bash|zsh|python3?)\b", line), f"{file}:{job}: {line.strip()}"


def test_the_kubeconform_archive_is_checksum_verified_before_it_is_extracted():
    scripts = [step["run"] for _, _, step in _steps() if "kubeconform" in step.get("run", "")]
    assert scripts
    for script in scripts:
        assert "set -euo pipefail" in script
        assert re.search(r"KUBECONFORM_SHA256=[0-9a-f]{64}", script)
        assert script.index("sha256sum --check") < script.index("tar xzf")
        assert script.index("tar xzf") < script.index("./kubeconform")


def test_a_tampered_archive_fails_the_checksum_step(tmp_path):
    script = next(step["run"] for _, _, step in _steps() if "KUBECONFORM_SHA256" in step.get("run", ""))
    expected = re.search(r"KUBECONFORM_SHA256=([0-9a-f]{64})", script).group(1)
    archive = tmp_path / "kubeconform.tar.gz"
    archive.write_bytes(b"not the published archive")
    assert hashlib.sha256(archive.read_bytes()).hexdigest() != expected
    check = subprocess.run(
        ["sha256sum", "--check", "--strict", "-"], input=f"{expected}  kubeconform.tar.gz\n",
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert check.returncode != 0
