import nbformat

from backend.compiler import compile_notebook


def test_ldap_cairo_and_dlib_imports_get_their_build_dependencies_in_the_image(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(
        "import ldap\nimport cairo\nimport dlib\n\ndef f(n: int) -> int:\n    return n\n"
    )]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    out = tmp_path / "out"

    compile_notebook(str(path), str(out))

    dockerfile = (out / "Dockerfile").read_text()
    for package in ("build-essential", "libldap2-dev", "libsasl2-dev", "libcairo2-dev", "pkg-config", "cmake"):
        assert package in dockerfile, package
    requirements = (out / "requirements.txt").read_text().lower()
    for name in ("python-ldap", "pycairo", "dlib"):
        assert name in requirements, name
