import json
import re
from pathlib import Path


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


def _build_method_names(paths):
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
    """
    used_names = set()
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
    """
    # Load OpenAPI schema
    with open(openapi_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    paths = schema.get("paths", {})
    method_names = _build_method_names(paths)
    wait_method_names = _build_wait_method_names(method_names, paths)
    # Prepare client code lines
    lines = []
    lines.append("import os")
    lines.append("import time")
    lines.append("import requests")
    lines.append("")
    lines.append("class NotebookAPIClient:")
    lines.append("    def __init__(self, base_url: str, api_key: str = None):")
    lines.append("        self.base_url = base_url.rstrip('/')")
    lines.append("        # Generated endpoints require the same X-API-Key header the")
    lines.append("        # generated app itself defaults to (see NOTEBOOK_API_KEY in")
    lines.append("        # api_generator.py) so the client works out of the box locally.")
    lines.append("        self.api_key = api_key or os.getenv(")
    lines.append("            'NOTEBOOK_API_KEY', 'notebook-to-api-dev-key'")
    lines.append("        )")
    lines.append("")
    lines.append("    def get_task(self, task_id: str) -> dict:")
    lines.append('        """Fetch the current status/result of a background task."""')
    lines.append("        response = requests.get(")
    lines.append(f'            f"{{self.base_url}}/tasks/{{task_id}}",')
    lines.append('            headers={"X-API-Key": self.api_key},')
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
    for path, method_name in method_names.items():
        is_background = _is_background_path(paths[path])
        # Determine parameter schema (simple request body expecting JSON)
        lines.append(f"    def {method_name}(self, payload: dict):")
        if is_background:
            wait_name = wait_method_names[path]
            lines.append(f'        """Enqueue the `{path}` background task with JSON payload.')
            lines.append("")
            lines.append(
                '        Returns {"task_id": ..., "status": "processing"} '
                "immediately --"
            )
            lines.append(
                "        not the real result. Call get_task(task_id)/"
                "wait_for_task(task_id)"
            )
            lines.append(
                f"        yourself, or use {wait_name}(...) to submit and "
                'block until'
            )
            lines.append('        the real result is ready in one call."""')
        else:
            lines.append(f'        """Call the `{path}` endpoint with JSON payload."""')
        lines.append(f'        response = requests.post(')
        lines.append(f'            f"{{self.base_url}}{path}",')
        lines.append(f'            json=payload,')
        lines.append(f'            headers={{"X-API-Key": self.api_key}},')
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
            lines.append(
                f'        """Submit `{path}` and block until the '
                "background task finishes, returning its finished task "
                'record (see wait_for_task)."""'
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
    with open(openapi_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    paths = schema.get("paths", {})
    method_names = _build_method_names(paths)
    wait_method_names = _build_wait_method_names(method_names, paths)
    # Prepare client code lines
    lines = []
    lines.append("export interface NotebookAPIClientOptions {")
    lines.append("  apiKey?: string;")
    lines.append("}")
    lines.append("")
    lines.append("export class NotebookAPIClient {")
    lines.append("  private baseUrl: string;")
    lines.append("  private apiKey: string;")
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
    for path, method_name in method_names.items():
        is_background = _is_background_path(paths[path])
        lines.append("")
        if is_background:
            wait_name = wait_method_names[path]
            lines.append(f"  /** Enqueues `{path}` with JSON payload and returns")
            lines.append(
                '   * { task_id, status: "processing" } immediately -- not the '
                "real result."
            )
            lines.append(
                f"   * Call getTask(taskId)/waitForTask(taskId) yourself, or "
                f"use {wait_name}(...)"
            )
            lines.append("   * to submit and wait for the real result in one call. */")
        lines.append(
            f"  async {method_name}(payload: Record<string, unknown>): "
            "Promise<any> {"
        )
        lines.append(f'    return this.request("{path}", payload);')
        lines.append("  }")

        if is_background:
            wait_name = wait_method_names[path]
            lines.append("")
            lines.append(
                f"  /** Submits `{path}` and blocks until the background "
                "task finishes, returning its finished task record (see "
                "waitForTask). */"
            )
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
