"""The generated compose file must not fall back to the public dev API key."""
import shutil
import subprocess

import pytest

from backend.generator.docker_generator import docker_compose_content, env_example_content

ENV = [
    {"name": "NOTEBOOK_API_KEY", "default": "notebook-to-api-dev-key", "description": "key"},
    {"name": "NOTEBOOK_API_REQUIRE_CUSTOM_KEY", "default": "false", "description": "x"},
]


def test_compose_requires_api_key_without_fallback():
    compose = docker_compose_content("generated", ENV)
    assert "dev-key" not in compose
    assert "NOTEBOOK_API_KEY=${NOTEBOOK_API_KEY:?" in compose
    assert "NOTEBOOK_API_REQUIRE_CUSTOM_KEY=${NOTEBOOK_API_REQUIRE_CUSTOM_KEY:-false}" in compose


def test_env_example_has_no_usable_key():
    example = env_example_content(ENV)
    assert "dev-key" not in example
    assert "\n# NOTEBOOK_API_KEY=\n" in example


@pytest.mark.skipif(not shutil.which("docker"), reason="needs docker compose")
def test_compose_config_fails_without_key_and_passes_with_one(tmp_path):
    path = tmp_path / "docker-compose.yml"
    path.write_text(docker_compose_content("generated", ENV))
    base = ["docker", "compose", "-f", str(path), "config", "-q"]
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    missing = subprocess.run(base, capture_output=True, text=True, env=env)
    assert missing.returncode != 0 and "NOTEBOOK_API_KEY" in missing.stderr
    ok = subprocess.run(base, capture_output=True, text=True, env={**env, "NOTEBOOK_API_KEY": "x" * 8})
    assert ok.returncode == 0, ok.stderr


def test_compose_drops_capabilities_and_blocks_privilege_escalation():
    compose = docker_compose_content("generated", ENV)
    assert 'security_opt: ["no-new-privileges:true"]' in compose
    assert 'cap_drop: ["ALL"]' in compose
    if shutil.which("docker"):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docker-compose.yml"
            path.write_text(compose)
            out = subprocess.run(
                ["docker", "compose", "-f", str(path), "config"], capture_output=True, text=True,
                env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": tmp, "NOTEBOOK_API_KEY": "x" * 8},
            )
        assert out.returncode == 0, out.stderr
        assert "no-new-privileges:true" in out.stdout and "- ALL" in out.stdout
