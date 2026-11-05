"""A compiled project ships a .gitignore so a real .env (API key) is never committed."""
import os

from backend.compiler import compile_notebook

NB = os.path.join(os.path.dirname(__file__), "..", "notebooks", "sample.ipynb")


def test_compile_writes_gitignore_excluding_env(tmp_path):
    out = tmp_path / "app"
    compile_notebook(NB, str(out))
    lines = (out / ".gitignore").read_text().splitlines()
    assert ".env" in lines and "__pycache__/" in lines
    assert "!.env.example" in lines


def test_existing_gitignore_is_preserved(tmp_path):
    out = tmp_path / "app"
    compile_notebook(NB, str(out))
    (out / ".gitignore").write_text("mine\n")
    compile_notebook(NB, str(out))
    assert (out / ".gitignore").read_text() == "mine\n"
