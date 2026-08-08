import nbformat

from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells,
    strip_magic_commands
)


def test_extract_code_cells():

    notebook = load_notebook(
        "notebooks/sample.ipynb"
    )

    code_cells = extract_code_cells(notebook)

    assert len(code_cells) > 0


def test_strip_magic_commands_comments_out_line_magic():

    source = "%matplotlib inline\nimport matplotlib.pyplot as plt"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# %matplotlib inline\nimport matplotlib.pyplot as plt"


def test_strip_magic_commands_comments_out_cell_magic():

    source = "%%time\nx = 1 + 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# %%time\nx = 1 + 1"


def test_strip_magic_commands_comments_out_shell_escape():

    source = "!pip install pandas\nimport pandas as pd"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# !pip install pandas\nimport pandas as pd"


def test_strip_magic_commands_preserves_indentation():

    source = "if True:\n    %timeit x\n    y = 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "if True:\n    # %timeit x\n    y = 1"


def test_strip_magic_commands_leaves_plain_code_untouched():

    source = "def add(a, b):\n    return a + b"

    assert strip_magic_commands(source) == source


def test_strip_magic_commands_does_not_touch_modulo_operator():

    source = "remainder = 10 % 3"

    assert strip_magic_commands(source) == source


def test_extract_code_cells_strips_magics_from_notebook():

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "%matplotlib inline\ndef plot():\n    return 1"
        )
    )

    code_cells = extract_code_cells(notebook)

    assert len(code_cells) == 1
    assert "# %matplotlib inline" in code_cells[0]
    assert "def plot():" in code_cells[0]