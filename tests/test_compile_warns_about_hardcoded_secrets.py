import nbformat

from backend.inspector import print_compile_summary

KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCD"


def _compile(tmp_path, source, capsys):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(source)]
    path = tmp_path / "nb.ipynb"
    nbformat.write(nb, str(path))
    print_compile_summary(str(path), str(tmp_path / "out"))
    return capsys.readouterr().out


def test_compile_warns_about_a_literal_credential_without_echoing_its_value(tmp_path, capsys):
    out = _compile(
        tmp_path,
        f'import os\nOPENAI_API_KEY = "{KEY}"\nDB_PASSWORD = "hunter2hunter2"\n'
        "def f(x: int) -> int:\n    return x\n",
        capsys,
    )

    assert "Cell 1, line 2: OPENAI_API_KEY is assigned a literal credential" in out
    assert "Cell 1, line 3: DB_PASSWORD" in out
    assert KEY not in out and "hunter2hunter2" not in out


def test_environment_lookups_and_empty_or_short_values_are_not_flagged(tmp_path, capsys):
    out = _compile(
        tmp_path,
        'import os\nAPI_KEY = os.environ["API_KEY"]\nTOKEN = ""\nSHORT_TOKEN = "abc"\n'
        "def f(x: int) -> int:\n    return x\n",
        capsys,
    )

    assert "literal credential" not in out
