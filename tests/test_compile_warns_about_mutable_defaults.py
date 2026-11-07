import nbformat
from fastapi.testclient import TestClient

from backend.compiler import compile_notebook
from backend.inspector import print_compile_summary

SOURCE = (
    "def acc(x: int, bucket: list = []) -> list:\n"
    "    bucket.append(x)\n    return bucket\n"
    "def safe(x: int, t: tuple = (1,), n: int = 3) -> int:\n    return x\n"
)


def _notebook(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    return path


def test_compile_warns_that_a_mutable_default_is_not_shared_between_requests(tmp_path, capsys):
    path = _notebook(tmp_path)

    print_compile_summary(str(path), str(tmp_path / "out"))

    out = capsys.readouterr().out
    assert "acc: parameter 'bucket' has a mutable default" in out
    assert "safe:" not in out.split("mutable default")[0].split("\n")[-1]
    assert out.count("mutable default") == 1


def test_the_warning_describes_what_the_compiled_app_actually_does(tmp_path, monkeypatch):
    path = _notebook(tmp_path)
    out = tmp_path / "pkg_out"
    compile_notebook(str(path), str(out))
    monkeypatch.syspath_prepend(str(tmp_path))
    import importlib

    app = importlib.import_module("pkg_out.app").app
    client = TestClient(app, headers={"X-API-Key": "notebook-to-api-dev-key"})

    assert client.post("/acc", json={"x": 1}).json() == {"result": [1]}
    assert client.post("/acc", json={"x": 2}).json() == {"result": [2]}


def test_star_args_and_kwargs_are_not_reported_as_mutable_defaults(tmp_path, capsys):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        "def f(a: int, *args, c: int = 3, **kw) -> int:\n    return a + c\n"
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))

    print_compile_summary(str(path), str(tmp_path / "out"))

    assert "mutable default" not in capsys.readouterr().out


def test_string_defaults_that_look_like_containers_are_not_mutable_defaults(tmp_path, capsys):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        'def g(cfg: str = "{}", tags: str = "[]", n: list = [1, 2], m: list = list()) -> str:\n    return cfg\n'
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))

    print_compile_summary(str(path), str(tmp_path / "out"))

    out = capsys.readouterr().out
    assert "parameter 'cfg' has a mutable" not in out
    assert "parameter 'tags' has a mutable" not in out
    assert "parameter 'n' has a mutable" in out
    assert "parameter 'm' has a mutable" in out
