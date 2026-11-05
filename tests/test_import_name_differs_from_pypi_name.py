"""Imports whose module name differs from the PyPI name must land in
requirements.txt under the PyPI name (the bare module name either does not
exist on PyPI or is an unrelated package)."""
import os

import nbformat

from backend.compiler import compile_notebook

CASES = {
    "absl": "absl-py", "discord": "discord.py", "tabula": "tabula-py",
    "camelot": "camelot-py", "fpdf": "fpdf2", "shapefile": "pyshp",
    "igraph": "python-igraph", "llama_cpp": "llama-cpp-python",
    "open_clip": "open-clip-torch", "spellchecker": "pyspellchecker",
    "memcache": "python-memcached", "vlc": "python-vlc",
    "tavily": "tavily-python", "sknetwork": "scikit-network",
    "weaviate": "weaviate-client", "osgeo": "GDAL", "ibis": "ibis-framework",
    "cassandra": "cassandra-driver", "corsheaders": "django-cors-headers",
    "kerastuner": "keras-tuner", "grpc_status": "grpcio-status",
    "grpc_tools": "grpcio-tools",
}


def test_requirements_use_pypi_names(tmp_path):
    src = "\n".join(f"import {m}" for m in CASES)
    src += "\ndef f(n: int) -> int:\n    '''d'''\n    return n\n"
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(src)]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    out = tmp_path / "app"
    compile_notebook(str(path), str(out))
    reqs = {
        line.split("==")[0].strip().lower()
        for line in (out / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    for module, dist in CASES.items():
        assert dist.lower() in reqs, (module, dist)
        assert module.lower() not in reqs or module.lower() == dist.lower()
