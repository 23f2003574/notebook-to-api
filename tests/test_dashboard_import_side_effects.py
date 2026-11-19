"""Importing the dashboard writes nothing into the working directory."""
import json
import os
import subprocess
import sys
from pathlib import Path

from backend.plugins.bootstrap import PluginBootstrap

ROOT = Path(__file__).resolve().parents[1]


def _run(code, cwd, **env):
    return subprocess.run([sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", **env})


def test_importing_the_dashboard_does_not_create_a_plugins_directory(tmp_path):
    done = _run("import backend.dashboard", tmp_path)
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "plugins").exists()


def test_discovery_does_not_create_a_missing_directory(tmp_path):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    missing = tmp_path / "nope"
    assert bootstrap.discover_plugins(str(missing)) == []
    assert not missing.exists()


def test_discovery_still_finds_manifests_in_an_existing_directory(tmp_path):
    bootstrap = PluginBootstrap()
    bootstrap.register()
    (tmp_path / "demo.plugin.json").write_text(
        json.dumps({"name": "side-effect-demo", "version": "1.0.0", "entry_point": "some.module"})
    )
    assert [m.name for m in bootstrap.discover_plugins(str(tmp_path))] == ["side-effect-demo"]


def test_plugins_directory_can_be_configured_through_the_environment(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "cfg.plugin.json").write_text(
        json.dumps({"name": "configured-dir", "version": "1.0.0", "entry_point": "some.module"})
    )
    done = _run(
        "from backend.plugins.bootstrap import PluginBootstrap\n"
        "b = PluginBootstrap(); b.register()\n"
        "print([m.name for m in b.discover_plugins()])", tmp_path,
        NOTEBOOK_API_PLUGINS_DIR=str(elsewhere),
    )
    assert done.returncode == 0, done.stderr
    assert "configured-dir" in done.stdout
    assert not (tmp_path / "plugins").exists()
