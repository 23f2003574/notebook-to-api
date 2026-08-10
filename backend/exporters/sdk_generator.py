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
        # Determine parameter schema (simple request body expecting JSON)
        lines.append(f"    def {method_name}(self, payload: dict):")
        lines.append(f'        """Call the `{path}` endpoint with JSON payload."""')
        lines.append(f'        response = requests.post(')
        lines.append(f'            f"{{self.base_url}}{path}",')
        lines.append(f'            json=payload,')
        lines.append(f'            headers={{"X-API-Key": self.api_key}},')
        lines.append(f'        )')
        lines.append("        response.raise_for_status()")
        lines.append("        return response.json()")
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
        lines.append("")
        lines.append(
            f"  async {method_name}(payload: Record<string, unknown>): "
            "Promise<any> {"
        )
        lines.append(f'    return this.request("{path}", payload);')
        lines.append("  }")
    lines.append("}")
    lines.append("")
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"TypeScript SDK generated at {output_path}")
