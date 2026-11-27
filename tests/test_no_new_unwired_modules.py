"""Ratchet against placeholder modules nothing uses.

A backend module must be reachable from an entry point (CLI, dashboard, app, serve, compiler,
inspector), be a public lazy export of `backend.generator`, or be listed in
tests/unwired_modules_baseline.txt -- the debt that existed when this guard was added. The baseline
may only shrink: a new unwired module fails here, and so does a baseline entry that is gone or now
wired (remove it). Static analysis includes imports inside functions, so lazy imports count."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).with_name("unwired_modules_baseline.txt")
ENTRY_POINTS = (
    "backend.cli", "backend.dashboard", "backend.app", "backend.serve", "backend.compiler",
    "backend.inspector", "backend.dashboard_healthcheck",
)


def _modules():
    mods = {}
    for path in (ROOT / "backend").rglob("*.py"):
        name = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        mods[name[: -len(".__init__")] if name.endswith(".__init__") else name] = path
    return mods


def _imports(path, module):
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                base = ".".join(parts + ([base] if base else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def unwired_modules():
    mods = _modules()
    graph = {name: {i for i in _imports(path, name) if i in mods} for name, path in mods.items()}
    reachable, stack = set(), [e for e in ENTRY_POINTS if e in mods]
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        stack.extend(graph[name])
        parts = name.split(".")
        stack.extend(".".join(parts[:i]) for i in range(1, len(parts)) if ".".join(parts[:i]) in mods)
    exported = set(re.findall(r'": "([a-z_0-9]+)"', (ROOT / "backend/generator/__init__.py").read_text()))
    return sorted(
        name for name, path in mods.items()
        if name not in reachable and path.name != "__init__.py"
        and not (name.startswith("backend.generator.") and name.rpartition(".")[2] in exported)
    )


def _baseline():
    return {line.strip() for line in BASELINE.read_text().splitlines() if line.strip() and not line.startswith("#")}


def test_no_new_unwired_modules():
    new = sorted(set(unwired_modules()) - _baseline())
    assert not new, (
        "these backend modules are not reachable from any entry point and are not exported by "
        f"backend.generator: {new}. Wire them in (or delete them); do not add to the baseline."
    )


def test_baseline_only_shrinks():
    stale = sorted(_baseline() - set(unwired_modules()))
    assert not stale, f"remove these now-wired or deleted modules from {BASELINE.name}: {stale}"


def _text_corpus():
    """Every source, test and doc file's text, keyed by repo-relative path (the baseline excluded)."""
    corpus = {}
    for pattern in ("backend/**/*.py", "tests/**/*.py", "*.md", "frontend/src/**/*.js*", ".github/**/*", "pyproject.toml"):
        for path in ROOT.glob(pattern):
            if path.is_file() and path != BASELINE:
                try:
                    corpus[path.relative_to(ROOT).as_posix()] = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
    return corpus


def orphaned_modules():
    """Unwired modules whose name no other file in the repository mentions at all: not imported, not
    exported, not tested, not documented. Nothing can depend on them, so they are safe to delete;
    a module another unwired module, a package __init__, a test or a document names is not an orphan."""
    corpus = _text_corpus()
    found = []
    for module in unwired_modules():
        own = module.replace(".", "/") + ".py"
        word = re.compile(r"\b" + re.escape(module.rpartition(".")[2]) + r"\b")
        if not any(path != own and (module in text or word.search(text)) for path, text in corpus.items()):
            found.append(module)
    return found


def test_no_unwired_module_is_referenced_by_nothing():
    assert not orphaned_modules(), (
        "these modules are unreachable and nothing mentions them anywhere; delete them and remove them from "
        f"{BASELINE.name}: {orphaned_modules()}"
    )
