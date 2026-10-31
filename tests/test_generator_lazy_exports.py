import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_python(code):
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_the_generator_package_does_not_load_the_web_stack():
    """`backend.generator` used to import all of its 236 submodules up front,
    pulling in FastAPI and the AI/observability engines for any command that
    only needed the compiler (`notebook-to-api --version` took over a second)."""
    result = _run_python(
        "import sys, backend.generator\n"
        "heavy = [m for m in ('fastapi', 'starlette', 'uvicorn', 'pandas') if m in sys.modules]\n"
        "print(heavy, len(sys.modules))"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("[]"), result.stdout


def test_compiler_import_stays_clear_of_the_pipeline_engines():
    result = _run_python(
        "import sys, backend.compiler\n"
        "print([m for m in sys.modules if m.startswith('backend.ai')])"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_every_exported_name_resolves_to_the_object_in_its_own_submodule():
    import backend.generator as generator

    assert len(generator._EXPORTS) > 400
    for name, module_name in generator._EXPORTS.items():
        expected = getattr(importlib.import_module(f"backend.generator.{module_name}"), name)
        assert getattr(generator, name) is expected, name
        assert name in dir(generator)


def test_exports_cover_every_name_a_submodule_defines_for_the_package():
    """Each _EXPORTS entry must point at a real module that defines the name
    (a typo would otherwise only show up when that name is first used)."""
    import backend.generator as generator

    for name, module_name in generator._EXPORTS.items():
        source = (PROJECT_ROOT / "backend" / "generator" / f"{module_name}.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        } | {
            target.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        } | {
            (alias.asname or alias.name) for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        assert name in defined, f"{module_name} does not define {name}"


def test_from_import_and_unknown_names_behave_like_a_normal_module():
    from backend.generator import PipelineRouteGenerator  # noqa: F401
    import backend.generator as generator

    with pytest.raises(AttributeError, match="no attribute 'DefinitelyNotThere'"):
        generator.DefinitelyNotThere
    with pytest.raises(ImportError):
        from backend.generator import DefinitelyNotThere  # noqa: F401


def test_star_import_still_provides_every_public_generator_name():
    """`from backend.generator import *` imported every name when the package
    loaded its submodules eagerly; lazy loading made it import nothing, and
    plain `from backend.generator import X` must keep working alongside it."""
    result = _run_python(
        "from backend.generator import PipelineRouteGenerator\n"
        "ns = {}\n"
        "exec('from backend.generator import *', ns)\n"
        "import backend.generator as g\n"
        "missing = [n for n in g._EXPORTS if n not in ns]\n"
        "print(len(g._EXPORTS), missing[:3], ns['PipelineRouteGenerator'] is PipelineRouteGenerator)"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("[] True"), result.stdout
