import json

import pytest

from backend.parser.notebook_parser import load_notebook


def test_old_v3_notebook_error_names_the_file(tmp_path):
    path = tmp_path / "legacy.ipynb"
    path.write_text(json.dumps({
        "cells": [{"cell_type": "code", "source": "x = 1", "metadata": {}}],
        "nbformat": 3,
        "nbformat_minor": 0,
    }))

    with pytest.raises(Exception) as excinfo:
        load_notebook(str(path))

    assert type(excinfo.value).__name__ == "ValidationError"
    assert str(path) in str(excinfo.value)
