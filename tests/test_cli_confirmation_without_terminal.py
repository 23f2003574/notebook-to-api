"""A confirmation prompt with no terminal to answer it must fail with a
message, not an EOFError traceback."""
import subprocess
import sys

import pytest

from backend import cli


def test_eof_becomes_a_clean_exit_naming_the_flag(monkeypatch):
    def eof(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(SystemExit) as caught:
        cli._confirmation_input("Delete? ")
    assert "--yes" in str(caught.value)


def test_a_typed_answer_is_returned(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert cli._confirmation_input("Delete? ") == "y"


@pytest.mark.parametrize("args", [["delete", "x.ipynb"], ["delete", "--all"]])
def test_delete_without_a_terminal_exits_1_without_a_traceback(args):
    result = subprocess.run(
        [sys.executable, "-m", "backend.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr and "--yes" in result.stderr


def test_every_confirmation_prompt_uses_the_wrapper():
    import re
    source = open(cli.__file__, encoding="utf-8").read()
    assert not re.search(r"answer = input\(", source)
