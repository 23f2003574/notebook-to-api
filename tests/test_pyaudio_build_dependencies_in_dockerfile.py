import nbformat

from backend.compiler import compile_notebook


def test_notebook_importing_pyaudio_gets_compiler_and_portaudio_headers_in_the_image(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        "import pyaudio\n\ndef channels(n: int) -> int:\n    return n\n"
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    out = tmp_path / "out"

    compile_notebook(str(path), str(out))

    dockerfile = (out / "Dockerfile").read_text()
    assert "build-essential" in dockerfile
    assert "portaudio19-dev" in dockerfile
    assert "pyaudio" in (out / "requirements.txt").read_text().lower()
