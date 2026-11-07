import nbformat

from backend.compiler import compile_notebook


def _dockerfile(tmp_path, source):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(source)]
    nbformat.write(nb, str(tmp_path / "nb.ipynb"))
    out = tmp_path / "out"
    compile_notebook(str(tmp_path / "nb.ipynb"), str(out))
    return (out / "Dockerfile").read_text()


def test_pyodbc_import_gets_unixodbc_so_the_import_works_in_the_slim_image(tmp_path):
    text = _dockerfile(tmp_path, "import pyodbc\n\ndef f(a: int) -> int:\n    return a\n")

    assert "unixodbc" in text
    assert text.index("apt-get install") < text.index("pip install")


def test_notebooks_without_pyodbc_do_not_install_it(tmp_path):
    assert "unixodbc" not in _dockerfile(tmp_path, "def f(a: int) -> int:\n    return a\n")


def test_tesserocr_import_gets_the_headers_pip_compiles_it_against(tmp_path):
    text = _dockerfile(tmp_path, "import tesserocr\n\ndef f(a: int) -> int:\n    return a\n")

    for package in ("build-essential", "libtesseract-dev", "libleptonica-dev"):
        assert package in text
    assert text.index("apt-get install") < text.index("pip install")
