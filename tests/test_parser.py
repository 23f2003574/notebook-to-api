from backend.parser.notebook_parser import (
    load_notebook,
    extract_code_cells
)


def test_extract_code_cells():

    notebook = load_notebook(
        "notebooks/sample.ipynb"
    )

    code_cells = extract_code_cells(notebook)

    assert len(code_cells) > 0