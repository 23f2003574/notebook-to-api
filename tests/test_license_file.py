"""Release check: every generated app declares the MIT license in its OpenAPI
metadata, so the repository must ship a real MIT LICENSE (it was empty)."""
from pathlib import Path

from backend.generator.api_generator import generate_fastapi_code

LICENSE = Path(__file__).resolve().parent.parent / "LICENSE"


def test_license_file_is_not_empty_and_is_mit():
    text = LICENSE.read_text()
    assert text.startswith("MIT License")
    assert "Copyright (c)" in text
    assert "Permission is hereby granted" in text


def test_generated_apps_declare_the_license_the_repo_ships():
    code = generate_fastapi_code([{"name": "add", "args": [], "return_type": "int"}])
    assert 'license_info={"name": "MIT"}' in code
