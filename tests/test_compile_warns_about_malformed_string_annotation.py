import nbformat

from backend.inspector import print_compile_summary


def _notebook(path, source):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(source)]
    nbformat.write(nb, str(path))


def test_compile_names_function_and_parameter_for_a_malformed_string_annotation(tmp_path, capsys):
    nb = tmp_path / "nb.ipynb"
    _notebook(nb, "def g(x: 'Undefined[') -> int:\n    return 1\n")

    print_compile_summary(str(nb), str(tmp_path / "out"))

    out = capsys.readouterr().out
    assert "g: the parameter 'x' annotation 'Undefined[' is a string" in out


def test_valid_forward_reference_string_is_not_flagged(tmp_path, capsys):
    nb = tmp_path / "nb.ipynb"
    _notebook(nb, "def g(x: 'Foo') -> 'list[int]':\n    return [1]\n")

    print_compile_summary(str(nb), str(tmp_path / "out"))

    assert "not a valid Python expression" not in capsys.readouterr().out
