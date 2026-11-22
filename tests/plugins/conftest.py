import pytest


@pytest.fixture(autouse=True)
def _permit_test_plugin_modules(monkeypatch, tmp_path):
    """The plugin endpoints deny imports and directories unless the operator permits them (see
    plugin_security, covered by tests/test_experimental_routers.py). These tests exercise the endpoints'
    own behaviour with generated throwaway modules, so permit those here."""
    monkeypatch.setattr("backend.plugins.plugin_loader.check_entry_point", lambda entry_point: None)
    monkeypatch.setattr("backend.plugins.plugin_lifecycle.check_entry_point", lambda entry_point: None)
    monkeypatch.setenv("NOTEBOOK2API_PLUGIN_ROOTS", str(tmp_path))
