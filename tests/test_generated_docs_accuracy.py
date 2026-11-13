"""Generated .env.example, docker-compose.yml and README match what the app really reads."""
import re

import nbformat
import pytest

from backend.compiler import compile_notebook

SOURCE = "# notebook-to-api: background\ndef work(x: int) -> int:\n    return x\n"
READ = re.compile(r'(?:os\.getenv|_env_[a-z]+)\(\s*["\'](NOTEBOOK_API_[A-Z_]+)["\']\s*,\s*["\']([^"\']*)["\']')


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("docs")
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SOURCE)]
    nbformat.write(nb, str(tmp / "nb.ipynb"))
    out = tmp / "docs_app"
    compile_notebook(str(tmp / "nb.ipynb"), str(out))
    text = {n: (out / n).read_text(encoding="utf-8") for n in (".env.example", "docker-compose.yml", "README.md", "app.py")}
    runtime = dict(READ.findall(text["app.py"]))
    return text, runtime


def test_runtime_reads_the_settings_users_are_told_about(project):
    text, runtime = project
    for needed in ("NOTEBOOK_API_KEY", "NOTEBOOK_API_ISOLATE_EXECUTION", "NOTEBOOK_API_TASK_STORE_PATH",
                   "NOTEBOOK_API_MAX_TASK_RESULT_BYTES", "NOTEBOOK_API_TASK_TTL_SECONDS",
                   "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB", "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS"):
        assert needed in runtime


def test_every_documented_name_exists_in_the_runtime(project):
    text, runtime = project
    for name in ("README.md", ".env.example"):
        for var in set(re.findall(r"NOTEBOOK_API_[A-Z_]+", text[name])):
            assert var in runtime, f"{name} documents {var}, which the app never reads"
    compose_only = {"NOTEBOOK_API_BIND_ADDRESS"}  # read by docker compose itself, not the app
    for var in set(re.findall(r"NOTEBOOK_API_[A-Z_]+", text["docker-compose.yml"])) - compose_only:
        assert var in runtime


def test_documented_defaults_match_runtime_defaults(project):
    text, runtime = project
    compose = dict(re.findall(r"- (NOTEBOOK_API_[A-Z_]+)=\$\{[A-Z_]+:-([^}]*)\}", text["docker-compose.yml"]))
    example = dict(re.findall(r"^#? ?(NOTEBOOK_API_[A-Z_]+)=(.*)$", text[".env.example"], re.M))
    readme = dict(re.findall(r"^- `(NOTEBOOK_API_[A-Z_]+)` \(default: `([^`]*)`\)", text["README.md"], re.M))
    # deliberately different, checked below / in test_generated_task_persistence_deployment.py
    special = {"NOTEBOOK_API_KEY", "NOTEBOOK_API_PUBLIC_URL", "NOTEBOOK_API_TASK_STORE_PATH"}
    for name, default in runtime.items():
        if name in special:
            continue
        assert compose.get(name) == default, f"compose default for {name}"
        assert example.get(name) == default, f".env.example default for {name}"
        assert readme.get(name) == default, f"README default for {name}"
    assert readme["NOTEBOOK_API_KEY"] == runtime["NOTEBOOK_API_KEY"]  # the app's own fallback


def test_required_key_is_represented_accurately_everywhere(project):
    text, _ = project
    assert "NOTEBOOK_API_KEY=${NOTEBOOK_API_KEY:?" in text["docker-compose.yml"]
    assert "dev-key" not in text["docker-compose.yml"] and "\n# NOTEBOOK_API_KEY=\n" in text[".env.example"]
    assert "exception is NOTEBOOK_API_KEY" in text[".env.example"]
    assert "must be set" in text["README.md"].split("## Configuration", 1)[1][:400]
    assert "will not start until this is set" in text["README.md"]


def test_isolation_docs_state_platform_limits_and_no_unimplemented_features(project):
    text, _ = project
    readme = text["README.md"]
    assert "RLIMIT_AS" in readme and "RLIMIT_CPU" in readme
    assert "Async functions are not isolated" in readme
    for invented in ("autoscal", "NOTEBOOK_API_REDIS", "NOTEBOOK_API_DATABASE", "NOTEBOOK_API_CELERY"):
        assert invented not in readme and invented not in text["docker-compose.yml"]


SYNC_SOURCE = "def double(x: int) -> int:\n    return x * 2\n"


@pytest.fixture(scope="module")
def sync_project(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("docs_sync")
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(SYNC_SOURCE)]
    nbformat.write(nb, str(tmp / "nb.ipynb"))
    out = tmp / "sync_app"
    compile_notebook(str(tmp / "nb.ipynb"), str(out))
    return {n: (out / n).read_text(encoding="utf-8") for n in (".env.example", "docker-compose.yml", "README.md")}


def test_readme_curl_example_does_not_use_the_public_dev_key(sync_project):
    readme = sync_project["README.md"]
    assert "-d '" in readme and "curl -X POST http://localhost:8000/double" in readme  # the example exists
    assert "X-API-Key: notebook-to-api-dev-key" not in readme


def test_readme_curl_example_reads_the_configured_key_safely(sync_project):
    curl = sync_project["README.md"].split("curl -X POST http://localhost:8000/double", 1)[1].split("```", 1)[0]
    # Double-quoted so the shell expands it; ${VAR:?} fails loudly instead of sending an empty key.
    assert '-H "X-API-Key: ${NOTEBOOK_API_KEY:?' in curl
    assert "-H 'Content-Type: application/json'" in curl


def test_readme_explains_where_the_key_comes_from_and_it_matches_compose_and_env(sync_project):
    readme, compose, env = (sync_project[n] for n in ("README.md", "docker-compose.yml", ".env.example"))
    intro = readme.split("## Calling an endpoint", 1)[1].split("```bash", 1)[0]
    assert "NOTEBOOK_API_KEY" in intro and ".env" in intro and ".env.example" in intro and "export" in intro.lower()
    # The same variable, with no fallback, is what compose requires and .env.example asks for.
    assert "NOTEBOOK_API_KEY=${NOTEBOOK_API_KEY:?" in compose
    assert re.search(r"^# NOTEBOOK_API_KEY=$", env, re.M)
