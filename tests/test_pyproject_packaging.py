import importlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_console_script_points_at_a_real_callable():
    target = PYPROJECT["project"]["scripts"]["notebook-to-api"]
    module, _, attr = target.partition(":")

    assert callable(getattr(importlib.import_module(module), attr))


def test_requires_python_matches_the_lowest_ci_version():
    import re

    ci = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[(.*?)\]", ci).group(1)
    lowest = min(tuple(int(p) for p in v.strip(' "').split(".")) for v in matrix.split(","))

    assert PYPROJECT["project"]["requires-python"] == ">=" + ".".join(map(str, lowest))


def test_dependencies_come_from_requirements_txt_and_the_package_covers_backend():
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["dependencies"] == {"file": ["requirements.txt"]}
    assert (ROOT / "requirements.txt").exists()
    find = PYPROJECT["tool"]["setuptools"]["packages"]["find"]
    assert find["include"] == ["backend*"] and find["namespaces"] is True
