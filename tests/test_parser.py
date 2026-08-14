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


def test_strip_magic_commands_comments_out_prefix_introspection():

    source = "?len\nx = 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# ?len\nx = 1"


def test_strip_magic_commands_comments_out_double_prefix_introspection():

    source = "??train_model\nx = 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# ??train_model\nx = 1"


def test_strip_magic_commands_comments_out_suffix_introspection():

    source = "train_model?\nx = 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# train_model?\nx = 1"


def test_strip_magic_commands_comments_out_double_suffix_introspection():

    source = "train_model??\nx = 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# train_model??\nx = 1"


def test_strip_magic_commands_comments_out_dotted_suffix_introspection():

    source = "np.random.seed?"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# np.random.seed?"


def test_strip_magic_commands_comments_out_called_suffix_introspection():

    source = "pd.DataFrame()?"

    cleaned = strip_magic_commands(source)

    assert cleaned == "# pd.DataFrame()?"


def test_strip_magic_commands_introspection_preserves_indentation():

    source = "if True:\n    train_model?\n    y = 1"

    cleaned = strip_magic_commands(source)

    assert cleaned == "if True:\n    # train_model?\n    y = 1"


def test_strip_magic_commands_leaves_question_mark_inside_string_untouched():
    """A "?" is not valid Python syntax anywhere outside of a string
    literal or comment, so any line consisting of *only* an
    attribute-chain expression plus a leading/trailing "?"/"??" is
    unambiguously an IPython introspection query. A line that merely
    *contains* a "?" as part of a larger, otherwise-valid statement (e.g.
    a string literal ending in "?") must not be touched.
    """

    source = 'msg = "wait?"'

    assert strip_magic_commands(source) == source


def test_strip_magic_commands_leaves_assignment_ending_in_digit_and_question_mark_untouched():

    source = "x = 5?"

    assert strip_magic_commands(source) == source


def test_extract_code_cells_strips_introspection_query_from_notebook():

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "train_model?\ndef train_model():\n    return 1"
        )
    )

    code_cells = extract_code_cells(notebook)

    assert len(code_cells) == 1
    assert "# train_model?" in code_cells[0]
    assert "def train_model():" in code_cells[0]