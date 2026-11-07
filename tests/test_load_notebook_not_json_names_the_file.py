import pytest

from backend.parser.notebook_parser import load_notebook


@pytest.mark.parametrize("text", ["", "API_KEY=sk-live-123\nthis is not json"])
def test_not_json_error_names_the_file_and_does_not_echo_its_content(tmp_path, text):
    path = tmp_path / "broken.ipynb"
    path.write_text(text)

    with pytest.raises(ValueError) as excinfo:
        load_notebook(str(path))

    message = str(excinfo.value)
    assert str(path) in message
    assert "not valid JSON" in message
    assert "sk-live-123" not in message
