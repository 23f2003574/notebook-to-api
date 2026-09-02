import json
import re
from pathlib import Path


# Client methods generate_python_sdk/generate_typescript_sdk always emit
# themselves -- get_task/wait_for_task (task polling) and
# list_tasks/delete_task/delete_completed_tasks/delete_failed_tasks (task
# management) -- independent of _build_method_names' per-path loop below.
# Unlike the built-in server-side routes these call (RESERVED_INFRASTRUCTURE_
# NAMES in api_generator.py), the *client* method names below have no
# equivalent guard stopping a notebook function from being named exactly
# one of them: "wait_for_task" or "list_tasks" compiles into the server
# fine (it isn't a reserved identifier there), but would silently shadow
# this exact hardcoded client method at class-body evaluation time --
# confirmed: a notebook exposing a `wait_for_task` endpoint produced two
# `def wait_for_task(...)` methods, with the second (the notebook's own,
# taking a `payload: dict`) overwriting the real polling helper, breaking
# every *other* background endpoint's *_and_wait companion, which calls
# self.wait_for_task(...) internally.
PYTHON_RESERVED_CLIENT_METHOD_NAMES = frozenset({
    "get_task", "wait_for_task", "list_tasks", "delete_task",
    "delete_completed_tasks", "delete_failed_tasks",
    "health", "ready", "info", "metrics", "uptime",
    "auth_status", "auth_info", "auth_validate",
    # Confirmed exploitable: base_url/api_key/timeout are the client's
    # own __init__-set *instance attributes* (self.base_url, self.api_key,
    # self.timeout -- every other method here reads them for exactly
    # that reason), not just names an unrelated method happened to share.
    # A notebook path sanitizing to one of these (e.g. "/base_url")
    # compiled into a same-named client *method* fine -- but an instance
    # attribute set in __init__ shadows a class-level method of the same
    # name on attribute lookup, so `self.base_url` from then on resolves
    # to the string, not the method: calling client.base_url(...) fails
    # with "'str' object is not callable", and nothing about the method
    # definition itself signals why.
    "base_url", "api_key", "timeout",
})

# Same hazard as PYTHON_RESERVED_CLIENT_METHOD_NAMES above, for the
# TypeScript client's own hardcoded method names -- method names aren't
# case-converted from the notebook function/path they came from (see
# _method_name_from_path below), so a notebook function named e.g.
# "waitForTask" would collide with this client's own waitForTask exactly
# the same way "wait_for_task" collides with the Python client's.
TYPESCRIPT_RESERVED_CLIENT_METHOD_NAMES = frozenset({
    "getTask", "waitForTask", "listTasks", "deleteTask",
    "deleteCompletedTasks", "deleteFailedTasks",
    "health", "ready", "info", "metrics", "uptime",
    "authStatus", "authInfo", "authValidate",
    # Same hazard as PYTHON_RESERVED_CLIENT_METHOD_NAMES's base_url/
    # api_key/timeout above: baseUrl/apiKey/timeoutMs are this client's
    # own private instance fields (this.baseUrl, this.apiKey,
    # this.timeoutMs), and a class can't declare a field and a method
    # under the same identifier -- "Duplicate identifier" at TypeScript
    # compile time, not just a same-named method colliding.
    "baseUrl", "apiKey", "timeoutMs",
})


def _method_name_from_path(path: str) -> str:
    """Convert an API path into a valid Python identifier.

    Notebook-derived endpoints are always single-segment (e.g.
    '/train_model'), but a real compiled app also exposes built-in
    multi-segment paths such as '/tasks/cleanup' and '/tasks/reset'. A
    naive `lstrip('/').replace('-', '_')` leaves the '/' in place, which
    produces an invalid method definition like `def tasks/cleanup(...)`.
    Replacing every run of non-identifier characters (slashes, hyphens,
    path-parameter braces) with a single underscore keeps names readable
    and always syntactically valid.
    """
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", path.strip("/")).strip("_")

    if not name:
        name = "root"
    elif name[0].isdigit():
        name = f"_{name}"

    return name


def _is_background_path(methods):
    """Whether `methods` (a path's {"post": {...}, ...} operations dict
    from an OpenAPI schema this tool generated) is a background/task_id
    endpoint, per the "x-notebook-to-api-async" marker
    generate_fastapi_code already stamps onto its POST operation (see
    api_generator.py) -- the same flag POST /api/compile's own
    "endpoints" field and inspect_notebook_data's "endpoints" field
    already key off of, reused here instead of re-deriving it from
    LONG_RUNNING_KEYWORDS a third time.
    """
    return bool((methods.get("post") or {}).get("x-notebook-to-api-async"))


def _build_wait_method_names(method_names, paths):
    """A collision-free companion method name for each background path in
    `method_names`, submitting the task and blocking until it finishes.

    Before this, a background endpoint's generated method (e.g.
    train_model) looked identical to a synchronous one: it returned
    {"task_id": ..., "status": "processing"} immediately, with nothing in
    the generated client actually connecting that to get_task/
    wait_for_task -- a caller had to already know, from reading the
    OpenAPI docs separately, which methods needed polling at all.

    Derived from each path's own already-assigned method name with an
    "_and_wait" suffix, disambiguated against every name already in use
    (not just other "_and_wait" names) the same way _build_method_names
    disambiguates the base names themselves: a real notebook function
    could easily be named e.g. "train_model_and_wait" and collide with
    the synthesized companion name for "/train_model".
    """
    used_names = set(method_names.values())
    wait_method_names = {}

    for path, methods in paths.items():

        if not _is_background_path(methods):
            continue

        base_name = f"{method_names[path]}_and_wait"
        candidate = base_name
        suffix = 2

        while candidate in used_names:
            candidate = f"{base_name}_{suffix}"
            suffix += 1

        used_names.add(candidate)
        wait_method_names[path] = candidate

    return wait_method_names


def _load_openapi_schema(openapi_path):
    """Read and JSON-decode the OpenAPI schema at `openapi_path`.

    Both generate_python_sdk and generate_typescript_sdk read their input
    this same way, but previously did it inline as a bare `json.load(f)`
    with no handling of its own: a caller pointing --openapi (or POST
    /api/export-sdk) at a file that isn't valid JSON crashed with
    json.JSONDecodeError's raw, low-level message ("Expecting value: line
    1 column 1 (char 0)") -- no indication of *why*, even though the most
    likely real-world cause is one this tool itself creates: POST
    /api/export-openapi and the CLI's own `export-openapi --format yaml`
    write a YAML file this function was never able to read, so a caller
    who exported YAML and then pointed export-sdk at that exact file (a
    completely reasonable thing to try, since both commands read/write
    the same GENERATED_DIR by default) got a confusing crash instead of
    being told export-sdk only reads JSON schemas.
    """
    with open(openapi_path, "r", encoding="utf-8") as f:
        raw = f.read()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        hint = (
            " This looks like a YAML export (export-openapi --format "
            "yaml) -- export-sdk only reads JSON schemas; re-export with "
            "--format json (the default) first."
            if Path(openapi_path).suffix.lower() in (".yaml", ".yml")
            else ""
        )
        raise ValueError(
            f"'{openapi_path}' is not a valid OpenAPI JSON schema: {exc}.{hint}"
        ) from exc


def _operation_description(methods):
    """The description text FastAPI's OpenAPI schema already carries for
    this path's POST operation -- generate_fastapi_code always sets one
    (api_generator.py): either a notebook function's own docstring, when
    it wrote one, or this tool's own auto-generated fallback sentence.
    Before this, that text was read by nothing at all -- every generated
    client method's docstring/JSDoc comment was pure hardcoded boilerplate
    ("Call the `/path` endpoint with JSON payload.") with zero connection
    to what the endpoint actually does, even though the schema being read
    right here already carries real documentation for it.

    Returns "" (not None) when absent, so callers can build doc text with
    a plain truthiness check rather than an extra None check -- absent
    only for an OpenAPI schema this tool didn't generate itself
    (export-sdk accepts any --openapi file, not just one this tool
    produced).
    """
    return ((methods.get("post") or {}).get("description") or "").strip()


def _python_method_docstring(description, static_text):
    """Combine `description` (see _operation_description) with a client
    method's own static explanatory text into one docstring statement,
    repr()'d rather than embedded as a hand-written triple-quoted literal
    like this client's other, fully static docstrings (get_task,
    wait_for_task, ...): description is arbitrary, notebook-author-
    controlled text (or this tool's own auto-generated fallback, which
    already includes each parameter's bare name) that can legitimately
    contain a double quote, a triple-quote sequence, or a backslash --
    embedding it directly into a \"\"\"...\"\"\" literal would let that
    content close the docstring early and corrupt the rest of the
    generated client into a SyntaxError, the exact hazard already fixed
    for the compiled app itself (see e91b1fa).
    """
    doc = f"{description}\n\n{static_text}" if description else static_text
    return repr(doc)


def _jsdoc_lines(description, static_text_lines, indent="  "):
    """Build a `/** ... */` JSDoc comment block's lines, combining
    `description` (see _operation_description) with a method's own static
    explanatory text (`static_text_lines`: already-wrapped lines with no
    leading ` * ` prefix or `*/` terminator of their own).

    Unlike Python's repr() for _python_method_docstring above, JS/TS block
    comments have no escape mechanism at all: a literal "*/" anywhere
    inside `description` would close the comment early, corrupting
    whatever source follows it -- potentially even swallowing the method
    signature the comment was meant to document. Neutralized by inserting
    a space into any such sequence before it's ever embedded, the same
    defensive substitution doc-comment generators for user-controlled
    text commonly use, since there's no syntactically "safe" encoding of
    an arbitrary string inside a JSDoc block the way repr() provides for
    a Python string literal.
    """
    text_lines = []

    if description:
        text_lines.extend(description.replace("*/", "* /").split("\n"))
        text_lines.append("")

    text_lines.extend(static_text_lines)

    lines = [f"{indent}/**"]

    for line in text_lines:
        safe_line = line.replace("*/", "* /")
        lines.append(f"{indent} * {safe_line}".rstrip())

    lines.append(f"{indent} */")

    return lines


def _build_method_names(paths, reserved_names=frozenset()):
    """Map each POST path to a collision-free client method name.

    Two different paths can sanitize to the same identifier -- e.g. a
    notebook function literally named "tasks_cleanup" (path
    "/tasks_cleanup") collides with the always-present built-in
    "/tasks/cleanup" route, since both reduce to "tasks_cleanup" once
    slashes become underscores. Confirmed before this fix: the second
    `def`/method definition silently shadowed the first at class-body
    evaluation time (Python) or produced a duplicate-method TypeScript
    compile error, either way permanently hiding one endpoint from the
    generated SDK with no error surfaced anywhere.

    `reserved_names` (PYTHON_RESERVED_CLIENT_METHOD_NAMES or
    TYPESCRIPT_RESERVED_CLIENT_METHOD_NAMES, passed in by
    generate_python_sdk/generate_typescript_sdk) seeds the same
    disambiguation for the client's own hardcoded methods (get_task,
    wait_for_task, list_tasks, ...), which live outside this per-path
    loop entirely and so can't be discovered by scanning `paths` alone --
    without seeding, a path colliding with one of *those* names shadowed
    them instead of being renamed the same way two colliding paths
    already are.
    """
    used_names = set(reserved_names)
    method_names = {}

    for path, methods in paths.items():
        if not methods.get("post"):
            continue

        base_name = _method_name_from_path(path)
        candidate = base_name
        suffix = 2

        while candidate in used_names:
            candidate = f"{base_name}_{suffix}"
            suffix += 1

        used_names.add(candidate)
        method_names[path] = candidate

    return method_names


def generate_python_sdk(
    openapi_path: str = "generated/openapi.json",
    output_path: str = "generated/sdk/python_client.py",
):
    """Generate a minimal Python SDK client from a FastAPI OpenAPI schema.

    The generated client contains a ``NotebookAPIClient`` class with a method for each
    POST endpoint defined in the OpenAPI spec. Each method performs a ``requests.post``
    call to the corresponding endpoint and returns ``response.json()``.

    Long-running notebook functions (see LONG_RUNNING_KEYWORDS in
    api_generator.py) don't return their result directly -- their endpoint
    enqueues a background task and immediately returns
    ``{"task_id": ..., "status": "processing"}``. Before get_task/
    wait_for_task existed, a caller of the generated client had no way to
    actually retrieve that result short of hand-writing their own polling
    loop against GET /tasks/{task_id}, even though the client already
    knows the base_url and api_key needed to do it.

    Also always includes health/ready/info/metrics/uptime/auth_status/
    auth_info/auth_validate -- every compiled app's own built-in GET
    routes for liveness/readiness, service metadata, request metrics, and
    auth configuration (see RESERVED_INFRASTRUCTURE_NAMES in
    api_generator.py, which guarantees they exist and can never be
    shadowed by a notebook function). Like get_task/list_tasks above,
    these are hardcoded rather than derived from the OpenAPI paths loop
    below, which only ever emits a method for a POST path.
    """
    # Load OpenAPI schema
    schema = _load_openapi_schema(openapi_path)

    paths = schema.get("paths", {})
    method_names = _build_method_names(paths, PYTHON_RESERVED_CLIENT_METHOD_NAMES)
    wait_method_names = _build_wait_method_names(method_names, paths)
    # Prepare client code lines
    lines = []
    lines.append("import os")
    lines.append("import time")
    lines.append("import requests")
    lines.append("")
    lines.append("class NotebookAPIClient:")
    lines.append(
        "    def __init__(self, base_url: str, api_key: str = None, "
        "timeout: float = 30.0):"
    )
    lines.append("        self.base_url = base_url.rstrip('/')")
    lines.append("        # Generated endpoints require the same X-API-Key header the")
    lines.append("        # generated app itself defaults to (see NOTEBOOK_API_KEY in")
    lines.append("        # api_generator.py) so the client works out of the box locally.")
    lines.append("        self.api_key = api_key or os.getenv(")
    lines.append("            'NOTEBOOK_API_KEY', 'notebook-to-api-dev-key'")
    lines.append("        )")
    lines.append("        # requests has no default socket timeout of its own -- a call")
    lines.append("        # with none set can hang indefinitely on a server that accepts")
    lines.append("        # the connection but never responds (a stalled deploy target, a")
    lines.append("        # network partition, ...), with nothing on the client side to")
    lines.append("        # ever give up. wait_for_task's own `timeout` bounds its polling")
    lines.append("        # *loop*, but each individual request it (and every other method")
    lines.append("        # here) makes had no bound of its own until now.")
    lines.append("        self.timeout = timeout")
    lines.append("")
    lines.append("    def get_task(self, task_id: str) -> dict:")
    lines.append('        """Fetch the current status/result of a background task."""')
    lines.append("        response = requests.get(")
    lines.append(f'            f"{{self.base_url}}/tasks/{{task_id}}",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        )")
    lines.append("        response.raise_for_status()")
    lines.append("        return response.json()")
    lines.append("")
    lines.append(
        "    def wait_for_task(self, task_id: str, poll_interval: float = 1.0, "
        "timeout: float = 60.0) -> dict:"
    )
    lines.append(
        '        """Poll get_task(task_id) until its status leaves '
        '"processing", returning the finished task record. Raises '
        'TimeoutError if `timeout` seconds pass first."""'
    )
    lines.append("        deadline = time.time() + timeout")
    lines.append("        while True:")
    lines.append("            task = self.get_task(task_id)")
    lines.append("            if task.get('status') != 'processing':")
    lines.append("                return task")
    lines.append("            if time.time() >= deadline:")
    lines.append("                raise TimeoutError(")
    lines.append(
        '                    f"Task {task_id} did not complete within '
        '{timeout} seconds"'
    )
    lines.append("                )")
    lines.append("            time.sleep(poll_interval)")
    lines.append("")
    # list_tasks/delete_task/delete_completed_tasks/delete_failed_tasks are,
    # like get_task/wait_for_task above, hardcoded rather than derived from
    # the per-path loop below: every compiled app guarantees these exact
    # routes (see RESERVED_INFRASTRUCTURE_NAMES in api_generator.py, which
    # blocks a notebook function from ever redefining one of them), but
    # that loop only emits a method for POST paths, so a caller who wanted
    # to see what's still running, or clear out finished tasks, had no way
    # to do it through the generated client at all -- only get_task, for a
    # single already-known task_id. This is the same gap wait_for_task
    # closed for polling a single task, now closed for the rest of a
    # background task's lifecycle.
    lines.append("    def list_tasks(self) -> dict:")
    lines.append(
        '        """List every background task, with a status-count '
        'summary."""'
    )
    lines.append("        response = requests.get(")
    lines.append('            f"{self.base_url}/tasks",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        )")
    lines.append("        response.raise_for_status()")
    lines.append("        return response.json()")
    lines.append("")
    lines.append("    def delete_task(self, task_id: str) -> dict:")
    lines.append('        """Delete a single background task by id."""')
    lines.append("        response = requests.delete(")
    lines.append('            f"{self.base_url}/tasks/{task_id}",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        )")
    lines.append("        response.raise_for_status()")
    lines.append("        return response.json()")
    lines.append("")
    lines.append("    def delete_completed_tasks(self) -> dict:")
    lines.append('        """Delete every task with status \'completed\'."""')
    lines.append("        response = requests.delete(")
    lines.append('            f"{self.base_url}/tasks/completed",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        )")
    lines.append("        response.raise_for_status()")
    lines.append("        return response.json()")
    lines.append("")
    lines.append("    def delete_failed_tasks(self) -> dict:")
    lines.append('        """Delete every task with status \'failed\'."""')
    lines.append("        response = requests.delete(")
    lines.append('            f"{self.base_url}/tasks/failed",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        )")
    lines.append("        response.raise_for_status()")
    lines.append("        return response.json()")
    lines.append("")
    # health/ready/info/metrics/uptime/auth_status/auth_info/auth_validate
    # are, like get_task/list_tasks/... above, hardcoded rather than
    # derived from the per-path loop below: every compiled app guarantees
    # these exact GET routes too (see RESERVED_INFRASTRUCTURE_NAMES in
    # api_generator.py, which blocks a notebook function from ever
    # redefining any of them), but that loop only ever emits a method for
    # POST paths. A caller wanting a liveness/readiness probe, service
    # info, request metrics, or auth configuration through the generated
    # client itself -- e.g. to back a monitoring dashboard, or confirm the
    # client's own api_key will actually be accepted before calling a real
    # notebook endpoint with it -- previously had no way to do that short
    # of hand-writing the exact same requests.get call get_task already
    # demonstrates this client knows how to make.
    for infra_method_name, infra_path in (
        ("health", "/health"),
        ("ready", "/ready"),
        ("info", "/info"),
        ("metrics", "/metrics"),
        ("uptime", "/uptime"),
        ("auth_status", "/auth/status"),
        ("auth_info", "/auth/info"),
        ("auth_validate", "/auth/validate"),
    ):
        lines.append(f"    def {infra_method_name}(self) -> dict:")
        lines.append(f'        """GET {infra_path}."""')
        lines.append("        response = requests.get(")
        lines.append(f'            f"{{self.base_url}}{infra_path}",')
        lines.append('            headers={"X-API-Key": self.api_key},')
        lines.append("            timeout=self.timeout,")
        lines.append("        )")
        lines.append("        response.raise_for_status()")
        lines.append("        return response.json()")
        lines.append("")
    for path, method_name in method_names.items():
        is_background = _is_background_path(paths[path])
        description = _operation_description(paths[path])
        # Determine parameter schema (simple request body expecting JSON)
        lines.append(f"    def {method_name}(self, payload: dict):")
        if is_background:
            wait_name = wait_method_names[path]
            static_doc = (
                f"Enqueue the `{path}` background task with JSON payload.\n\n"
                'Returns {"task_id": ..., "status": "processing"} '
                "immediately -- not the real result. Call get_task(task_id)/"
                "wait_for_task(task_id) yourself, or use "
                f"{wait_name}(...) to submit and block until the real "
                "result is ready in one call."
            )
        else:
            static_doc = f"Call the `{path}` endpoint with JSON payload."
        lines.append(
            f"        {_python_method_docstring(description, static_doc)}"
        )
        lines.append(f'        response = requests.post(')
        lines.append(f'            f"{{self.base_url}}{path}",')
        lines.append(f'            json=payload,')
        lines.append(f'            headers={{"X-API-Key": self.api_key}},')
        lines.append(f'            timeout=self.timeout,')
        lines.append(f'        )')
        lines.append("        response.raise_for_status()")
        lines.append("        return response.json()")
        lines.append("")

        if is_background:
            wait_name = wait_method_names[path]
            lines.append(
                f"    def {wait_name}(self, payload: dict, "
                "poll_interval: float = 1.0, timeout: float = 60.0) -> dict:"
            )
            and_wait_static_doc = (
                f"Submit `{path}` and block until the background task "
                "finishes, returning its finished task record (see "
                "wait_for_task)."
            )
            lines.append(
                f"        {_python_method_docstring(description, and_wait_static_doc)}"
            )
            lines.append(f"        submitted = self.{method_name}(payload)")
            lines.append(
                "        return self.wait_for_task("
                "submitted['task_id'], poll_interval, timeout)"
            )
            lines.append("")
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Python SDK generated at {output_path}")


def generate_typescript_sdk(
    openapi_path: str = "generated/openapi.json",
    output_path: str = "generated/sdk/typescript_client.ts",
):
    """Generate a minimal TypeScript SDK client from a FastAPI OpenAPI schema.

    Mirrors :func:`generate_python_sdk`: the generated client contains a
    ``NotebookAPIClient`` class with a method for each POST endpoint defined
    in the OpenAPI spec. Each method performs a ``fetch`` call to the
    corresponding endpoint and returns the parsed JSON response.
    """
    # Load OpenAPI schema
    schema = _load_openapi_schema(openapi_path)

    paths = schema.get("paths", {})
    method_names = _build_method_names(
        paths, TYPESCRIPT_RESERVED_CLIENT_METHOD_NAMES
    )
    wait_method_names = _build_wait_method_names(method_names, paths)
    # Prepare client code lines
    lines = []
    lines.append("export interface NotebookAPIClientOptions {")
    lines.append("  apiKey?: string;")
    lines.append("  timeoutMs?: number;")
    lines.append("}")
    lines.append("")
    lines.append("export class NotebookAPIClient {")
    lines.append("  private baseUrl: string;")
    lines.append("  private apiKey: string;")
    lines.append("  private timeoutMs: number;")
    lines.append("")
    lines.append(
        "  constructor(baseUrl: string, options: NotebookAPIClientOptions = {}) {"
    )
    lines.append('    this.baseUrl = baseUrl.replace(/\\/+$/, "");')
    # Generated endpoints require the same X-API-Key header the generated
    # app itself defaults to (see NOTEBOOK_API_KEY in api_generator.py) so
    # the client works out of the box locally, matching the Python client.
    lines.append(
        '    this.apiKey = options.apiKey ?? "notebook-to-api-dev-key";'
    )
    # fetch() has no default timeout of its own -- a call with no signal
    # can hang indefinitely on a server that accepts the connection but
    # never responds (a stalled deploy target, a network partition, ...),
    # with nothing on the client side to ever give up. waitForTask's own
    # timeoutMs bounds its polling *loop*, but each individual request it
    # (and every other method here) makes had no bound of its own until
    # now -- matches the Python client's identical `timeout` addition.
    lines.append("    this.timeoutMs = options.timeoutMs ?? 30000;")
    lines.append("  }")
    lines.append("")
    lines.append(
        "  private async request(path: string, payload: unknown): Promise<any> {"
    )
    lines.append("    const response = await fetch(`${this.baseUrl}${path}`, {")
    lines.append('      method: "POST",')
    lines.append("      headers: {")
    lines.append('        "Content-Type": "application/json",')
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      body: JSON.stringify(payload),")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    lines.append(
        "      throw new Error(`Request to ${path} failed with status "
        "${response.status}`);"
    )
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    lines.append("")
    lines.append("  async getTask(taskId: string): Promise<any> {")
    lines.append(
        "    const response = await fetch(`${this.baseUrl}/tasks/${taskId}`, {"
    )
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    lines.append(
        "      throw new Error(`Request to /tasks/${taskId} failed with "
        "status ${response.status}`);"
    )
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    lines.append("")
    lines.append(
        "  async waitForTask(taskId: string, options: { pollIntervalMs?: "
        "number; timeoutMs?: number } = {}): Promise<any> {"
    )
    lines.append("    const pollIntervalMs = options.pollIntervalMs ?? 1000;")
    lines.append("    const timeoutMs = options.timeoutMs ?? 60000;")
    lines.append("    const deadline = Date.now() + timeoutMs;")
    lines.append("    while (true) {")
    lines.append("      const task = await this.getTask(taskId);")
    lines.append('      if (task.status !== "processing") {')
    lines.append("        return task;")
    lines.append("      }")
    lines.append("      if (Date.now() >= deadline) {")
    lines.append(
        "        throw new Error(`Task ${taskId} did not complete within "
        "${timeoutMs}ms`);"
    )
    lines.append("      }")
    lines.append(
        "      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));"
    )
    lines.append("    }")
    lines.append("  }")
    lines.append("")
    # Mirrors generate_python_sdk's list_tasks/delete_task/
    # delete_completed_tasks/delete_failed_tasks: hardcoded rather than
    # derived from the per-path loop below (which only emits a method for
    # POST paths), since every compiled app guarantees these exact routes
    # (see RESERVED_INFRASTRUCTURE_NAMES in api_generator.py). Closes the
    # same gap for the rest of a background task's lifecycle that
    # waitForTask already closed for polling a single known task.
    lines.append("  async listTasks(): Promise<any> {")
    lines.append("    const response = await fetch(`${this.baseUrl}/tasks`, {")
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    lines.append(
        "      throw new Error(`Request to /tasks failed with status "
        "${response.status}`);"
    )
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    lines.append("")
    lines.append("  async deleteTask(taskId: string): Promise<any> {")
    lines.append(
        "    const response = await fetch(`${this.baseUrl}/tasks/${taskId}`, {"
    )
    lines.append('      method: "DELETE",')
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    lines.append(
        "      throw new Error(`Request to /tasks/${taskId} failed with "
        "status ${response.status}`);"
    )
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    lines.append("")
    lines.append("  async deleteCompletedTasks(): Promise<any> {")
    lines.append(
        "    const response = await fetch(`${this.baseUrl}/tasks/completed`, {"
    )
    lines.append('      method: "DELETE",')
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    lines.append(
        "      throw new Error(`Request to /tasks/completed failed with "
        "status ${response.status}`);"
    )
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    lines.append("")
    lines.append("  async deleteFailedTasks(): Promise<any> {")
    lines.append(
        "    const response = await fetch(`${this.baseUrl}/tasks/failed`, {"
    )
    lines.append('      method: "DELETE",')
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    lines.append(
        "      throw new Error(`Request to /tasks/failed failed with "
        "status ${response.status}`);"
    )
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    # Mirrors generate_python_sdk's health/ready/info/metrics/uptime/
    # auth_status/auth_info/auth_validate above: hardcoded rather than
    # derived from the per-path loop below (which only emits a method for
    # POST paths), since every compiled app guarantees these exact GET
    # routes (see RESERVED_INFRASTRUCTURE_NAMES in api_generator.py).
    for infra_method_name, infra_path in (
        ("health", "/health"),
        ("ready", "/ready"),
        ("info", "/info"),
        ("metrics", "/metrics"),
        ("uptime", "/uptime"),
        ("authStatus", "/auth/status"),
        ("authInfo", "/auth/info"),
        ("authValidate", "/auth/validate"),
    ):
        lines.append("")
        lines.append(f"  async {infra_method_name}(): Promise<any> {{")
        lines.append(
            f"    const response = await fetch(`${{this.baseUrl}}{infra_path}`, {{"
        )
        lines.append("      headers: {")
        lines.append('        "X-API-Key": this.apiKey,')
        lines.append("      },")
        lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
        lines.append("    });")
        lines.append("    if (!response.ok) {")
        lines.append(
            f"      throw new Error(`Request to {infra_path} failed with "
            "status ${response.status}`);"
        )
        lines.append("    }")
        lines.append("    return response.json();")
        lines.append("  }")
    for path, method_name in method_names.items():
        is_background = _is_background_path(paths[path])
        description = _operation_description(paths[path])
        lines.append("")
        if is_background:
            wait_name = wait_method_names[path]
            static_text_lines = [
                f"Enqueues `{path}` with JSON payload and returns",
                '{ task_id, status: "processing" } immediately -- not the '
                "real result.",
                f"Call getTask(taskId)/waitForTask(taskId) yourself, or "
                f"use {wait_name}(...)",
                "to submit and wait for the real result in one call.",
            ]
        else:
            static_text_lines = [f"Calls the `{path}` endpoint with JSON payload."]
        lines.extend(_jsdoc_lines(description, static_text_lines))
        lines.append(
            f"  async {method_name}(payload: Record<string, unknown>): "
            "Promise<any> {"
        )
        lines.append(f'    return this.request("{path}", payload);')
        lines.append("  }")

        if is_background:
            wait_name = wait_method_names[path]
            lines.append("")
            and_wait_static_text_lines = [
                f"Submits `{path}` and blocks until the background task "
                "finishes, returning its finished task record (see "
                "waitForTask).",
            ]
            lines.extend(_jsdoc_lines(description, and_wait_static_text_lines))
            lines.append(
                f"  async {wait_name}(payload: Record<string, unknown>, "
                "options: { pollIntervalMs?: number; timeoutMs?: number } = "
                "{}): Promise<any> {"
            )
            lines.append(f"    const submitted = await this.{method_name}(payload);")
            lines.append(
                "    return this.waitForTask(submitted.task_id, options);"
            )
            lines.append("  }")
    lines.append("}")
    lines.append("")
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"TypeScript SDK generated at {output_path}")
