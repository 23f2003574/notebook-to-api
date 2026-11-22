"""The generated compose file probes /health with a start period an operator can raise."""
import nbformat
import yaml

from backend.compiler import compile_notebook


def _compose(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell("def f(a: int) -> int:\n    return a\n")]
    nbformat.write(nb, str(tmp_path / "n.ipynb"))
    compile_notebook(str(tmp_path / "n.ipynb"), str(tmp_path / "out"))
    return tmp_path / "out"


def test_compose_healthcheck_has_a_configurable_start_period(tmp_path):
    out = _compose(tmp_path)
    service = next(iter(yaml.safe_load((out / "docker-compose.yml").read_text())["services"].values()))
    check = service["healthcheck"]
    assert check["start_period"] == "${NOTEBOOK_API_HEALTHCHECK_START_PERIOD:-5s}"
    assert (check["interval"], check["timeout"], check["retries"]) == ("30s", "3s", 3)
    assert check["test"][:3] == ["CMD", "python", "-c"] and "/health" in check["test"][3] and "PORT" in check["test"][3]


def test_probe_matches_the_dockerfile_healthcheck_and_is_documented(tmp_path):
    out = _compose(tmp_path)
    service = next(iter(yaml.safe_load((out / "docker-compose.yml").read_text())["services"].values()))
    assert service["healthcheck"]["test"][3] in (out / "Dockerfile").read_text()
    assert "NOTEBOOK_API_HEALTHCHECK_START_PERIOD" in (out / ".env.example").read_text()
