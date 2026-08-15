import ast
import builtins
import typing
from pathlib import Path

# Top-level names the generated app itself defines. A notebook function
# sharing one of these names would be emitted as `def <name>(...)`,
# rebinding the real one at module-load time -- most dangerously
# "verify_api_key": every endpoint defined *after* such a collision gets
# `Depends(verify_api_key)` resolved (at def-statement time) against the
# notebook's own function instead of the real auth guard, silently
# disabling API-key authentication for the rest of the app with no error
# anywhere. Rejecting these outright avoids ever emitting that endpoint
# ordering trap.
RESERVED_INFRASTRUCTURE_NAMES = frozenset({
    "app", "TASKS", "API_KEYS", "API_KEY_HEADER_NAME", "START_TIME",
    "GENERATED_AT", "PYTHON_VERSION", "ALLOWED_ORIGINS",
    "MAX_REQUEST_BODY_BYTES", "MaxRequestBodySizeMiddleware",
    "MAX_PENDING_TASKS",
    "verify_api_key", "custom_openapi",
    "root", "health_check", "readiness_check", "auth_status", "auth_info",
    "validate_auth", "service_info", "metrics", "uptime",
    "get_task", "list_tasks", "delete_task", "cleanup_tasks",
    "delete_completed_tasks", "delete_failed_tasks", "reset_tasks",
    "notebook_module",
    # Confirmed exploitable: these two private helpers (both defined at
    # module scope, like every other name above) were missing here, so a
    # notebook function literally named "_evict_expired_tasks" or
    # "_run_background_task" compiled fine and silently overwrote the
    # real one at module-execution time -- Python has no protection
    # against redefining a name, the later `def` always wins. Every
    # *other* background endpoint's own submission still calls the exact
    # same shadowed name (`_evict_expired_tasks()` before enqueuing a new
    # task, `background_tasks.add_task(_run_background_task, ...)` to run
    # one), so this didn't just break the notebook's own colliding
    # endpoint -- it broke background task submission or execution
    # *entirely*, app-wide. Reproduced: a notebook exposing both
    # `_evict_expired_tasks(x: int) -> int` and an unrelated `train_model`
    # crashed `POST /train_model` itself with "TypeError:
    # _evict_expired_tasks() missing 1 required positional argument:
    # 'req'", nothing to do with train_model's own logic at all.
    "_evict_expired_tasks", "_run_background_task",
})


class ReservedFunctionNameError(ValueError):
    """A notebook function's name collides with an identifier the
    generated app itself defines."""


# Keywords indicating a function should be run as a background task
LONG_RUNNING_KEYWORDS = [
    "train",
    "process",
    "generate",
    "embed",
    "scrape",
]

def _call_arg_expr(arg):
    """Render a single argument for the notebook_module.<fn>(...) call.

    Keyword-only parameters (those after a bare `*`, e.g.
    `def train(data, *, epochs=10)`) cannot be passed positionally, so
    they must be forwarded as `name=req.name` rather than plain `req.name`.
    """
    if arg.get("kind") == "keyword_only":
        return f"{arg['name']}=req.{arg['name']}"
    return f"req.{arg['name']}"


_TYPING_EXPORTS = frozenset(
    name for name in dir(typing) if not name.startswith("_")
)
_BUILTIN_NAMES = frozenset(dir(builtins))


class _AnnotationNameQualifier(ast.NodeTransformer):
    """Rewrites bare names in a type-annotation AST so every name the
    generated Pydantic model references is actually resolvable.

    A name that belongs to `typing` (List, Dict, Optional, Union, ...) is
    left alone but recorded so the caller can emit the matching
    `from typing import ...` line. Any other name that isn't a Python
    builtin is assumed to come from the notebook itself -- a class/Enum it
    defines, or something it imported at module level -- since the
    function using it as an annotation lives in that same module, the bare
    name must already resolve there. It's rewritten to
    `notebook_module.<name>` (the alias the generated app already imports
    the notebook's runtime module under) instead of failing with a
    NameError/PydanticUserError when the model class is built.
    """

    def __init__(self):
        self.typing_names = set()

    def visit_Name(self, node):
        if node.id in _TYPING_EXPORTS:
            self.typing_names.add(node.id)
            return node

        if node.id in _BUILTIN_NAMES:
            return node

        return ast.copy_location(
            ast.Attribute(
                value=ast.Name(id="notebook_module", ctx=ast.Load()),
                attr=node.id,
                ctx=node.ctx,
            ),
            node,
        )


def _build_model_names(functions):
    """Map each function's name to a Pydantic request-model class name,
    guaranteed unique even when two function names collide once reduced
    to a class name (e.g. "get_data" and "Get_data" -- only the first
    character was ever uppercased, so both produced the identical class
    name "Get_dataRequest", and the second definition silently shadowed
    the first's fields: whichever endpoint referenced that name ended up
    validating requests against the *other* function's parameters).
    """
    used_names = set()
    model_names = {}

    for func in functions:
        func_name = func["name"]
        base_name = f"{func_name[0].upper()}{func_name[1:]}Request"
        candidate = base_name
        suffix = 2

        while candidate in used_names:
            candidate = f"{base_name}_{suffix}"
            suffix += 1

        used_names.add(candidate)
        model_names[func_name] = candidate

    return model_names


def _resolve_annotation_source(type_str):
    """Turn a raw `ast.unparse`d annotation string (as stored in
    arg["type"] by the parser) into source the generated app can actually
    evaluate, plus the set of `typing` names it needs imported.

    Before this, arg["type"] was written into the generated Pydantic model
    verbatim: `List[float]`, `Optional[str]`, `Dict[str, Any]`, or a
    notebook-defined class/Enum name all produced a field annotation
    referencing a name nothing in the generated file ever imports, which
    breaks model construction (`PredictRequest.model_json_schema()` /
    `model_rebuild()`) the first time FastAPI actually needs the schema --
    i.e. on the very first request or /docs load, not at compile time.
    """
    if not type_str:
        return "str", set()

    try:
        tree = ast.parse(type_str, mode="eval")
    except SyntaxError:
        return type_str, set()

    qualifier = _AnnotationNameQualifier()
    rewritten = qualifier.visit(tree)
    ast.fix_missing_locations(rewritten)

    return ast.unparse(rewritten), qualifier.typing_names


# Template for generating the FastAPI application source code
def generate_fastapi_code(functions, package_name="generated"):
    """Generate FastAPI app code for the given functions.

    Each function is examined; if its name contains any of the
    LONG_RUNNING_KEYWORDS, an endpoint is created that enqueues the
    function as a BackgroundTask and returns a task_id. Otherwise a
    regular synchronous endpoint is generated.

    package_name is the top-level package the generated app imports its
    runtime module from (`<package_name>.runtime.notebook_module`). It
    must match the basename of wherever this generated code actually gets
    written -- see compiler.package_name_for_output_dir.
    """
    colliding_names = sorted(
        {func["name"] for func in functions} & RESERVED_INFRASTRUCTURE_NAMES
    )
    if colliding_names:
        raise ReservedFunctionNameError(
            "Notebook function name(s) "
            f"{', '.join(colliding_names)} collide with identifiers the "
            "generated app itself defines (auth, task management, or "
            "infrastructure routes). Rename the function(s) in the "
            "notebook and recompile."
        )

    needed_typing_names = set()
    for func in functions:
        for arg in func.get("args", []):
            _, typing_names = _resolve_annotation_source(arg.get("type"))
            needed_typing_names |= typing_names

            if arg.get("has_default") and not arg.get(
                "default_is_literal", True
            ):
                _, default_typing_names = _resolve_annotation_source(
                    arg.get("default")
                )
                needed_typing_names |= default_typing_names

    model_names = _build_model_names(functions)

    lines = []
    # Imports for the generated FastAPI app
    lines.append(
        "from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Depends"
    )
    lines.append("from fastapi.middleware.cors import CORSMiddleware")
    lines.append("from fastapi.responses import JSONResponse")
    lines.append("from fastapi.encoders import jsonable_encoder")
    lines.append("import anyio.to_thread")
    lines.append("import functools")
    lines.append("import uuid")
    lines.append("import os")
    lines.append("import sys")
    lines.append("import inspect")
    lines.append("import hmac")
    lines.append("from datetime import datetime")
    lines.append("import time")
    lines.append("from pydantic import BaseModel, Field")
    if needed_typing_names:
        lines.append(f"from typing import {', '.join(sorted(needed_typing_names))}")
    lines.append(f"import {package_name}.runtime.notebook_module as notebook_module")
    lines.append("")
    lines.append(
        'app = FastAPI('
        'title="Notebook-to-API Generated Service", '
        'description="Automatically generated from notebook analysis.", '
        'version="1.0.0", '
        'contact={"name": "Notebook-to-API"}, '
        'license_info={"name": "MIT"}, '
        'servers=[{"url": "http://localhost:8000", '
        '"description": "Local development server"}]'
        ')'
    )
    lines.append("")
    lines.append("app.openapi_schema = None")
    lines.append("")
    # Every request this app accepts is authenticated via the X-API-Key
    # header (see verify_api_key below), never a cookie, so -- unlike the
    # dashboard API's own CORS setup in backend/dashboard.py, which has to
    # restrict allow_origins to an explicit list precisely because it
    # accepts credentialed (cookie-based) cross-origin requests --
    # reflecting an arbitrary Origin here carries no cross-site credential
    # risk. allow_credentials is explicitly False, which is also what
    # makes a "*" default safe (browsers refuse "*" together with
    # allow_credentials=True). Without this, the single most common way to
    # actually consume a deployed generated API -- a browser-based
    # frontend calling it directly -- was blocked by CORS with no way to
    # fix it short of hand-editing this generated file. Configurable via
    # NOTEBOOK_API_ALLOWED_ORIGINS (comma-separated) to lock this down for
    # a real deployment instead.
    lines.append(
        'ALLOWED_ORIGINS = ['
        'o.strip() for o in os.getenv('
        '"NOTEBOOK_API_ALLOWED_ORIGINS", "*"'
        ').split(",") if o.strip()'
        '] or ["*"]'
    )
    lines.append(
        "app.add_middleware("
        "CORSMiddleware, "
        "allow_origins=ALLOWED_ORIGINS, "
        "allow_credentials=False, "
        "allow_methods=['*'], "
        "allow_headers=['*']"
        ")"
    )
    lines.append("")
    # Every endpoint below accepts an arbitrary JSON request body with no
    # limit on its size -- unlike this very tool's own dashboard
    # /api/upload, which has always capped uploads at MAX_UPLOAD_BYTES
    # (see routes/upload.py) for exactly this reason. A deployed generated
    # app has no equivalent: one oversized request can consume unbounded
    # memory building the body before FastAPI/Pydantic ever gets a chance
    # to validate or reject it. Rejecting outright on a declared
    # Content-Length over the limit, before the body is read at all, is
    # the same "reject before reading" approach MAX_UPLOAD_BYTES already
    # uses. Matches the NOTEBOOK_API_* env-var convention this generated
    # app's other limits (API_KEYS, TASK_TTL_SECONDS, ALLOWED_ORIGINS)
    # already follow, defaulting to the same 10MB MAX_UPLOAD_BYTES already
    # defaults to.
    lines.append(
        'MAX_REQUEST_BODY_BYTES = int(os.getenv('
        '"NOTEBOOK_API_MAX_REQUEST_BYTES", '
        f'"{10 * 1024 * 1024}"'
        '))'
    )
    lines.append("")
    lines.append("class MaxRequestBodySizeMiddleware:")
    lines.append("    def __init__(self, app):")
    lines.append("        self.app = app")
    lines.append("")
    lines.append("    async def __call__(self, scope, receive, send):")
    lines.append("        if scope['type'] == 'http':")
    lines.append("            for name, value in scope.get('headers') or []:")
    lines.append("                if name != b'content-length':")
    lines.append("                    continue")
    lines.append("                try:")
    lines.append("                    too_large = int(value) > MAX_REQUEST_BODY_BYTES")
    lines.append("                except ValueError:")
    lines.append("                    break")
    lines.append("                if too_large:")
    lines.append("                    response = JSONResponse(")
    lines.append("                        {")
    lines.append("                            'detail': (")
    lines.append("                                'Request body exceeds the maximum allowed '")
    lines.append("                                f'size of {MAX_REQUEST_BODY_BYTES} bytes'")
    lines.append("                            )")
    lines.append("                        },")
    lines.append("                        status_code=413,")
    lines.append("                    )")
    lines.append("                    await response(scope, receive, send)")
    lines.append("                    return")
    lines.append("                break")
    lines.append("        await self.app(scope, receive, send)")
    lines.append("")
    lines.append("app.add_middleware(MaxRequestBodySizeMiddleware)")
    lines.append("")
    # Simple in‑memory task registry used by background endpoints
    lines.append("TASKS = {}")
    lines.append(
        'TASK_TTL_SECONDS = int(os.getenv('
        '"NOTEBOOK_API_TASK_TTL_SECONDS", '
        '"3600"'
        '))'
    )
    # _evict_expired_tasks bounds TASKS' *long-term* growth (nothing
    # older than TASK_TTL_SECONDS survives), but a burst of background
    # requests arriving faster than that TTL still grows TASKS without
    # limit in the meantime -- eviction alone doesn't stop a client
    # (malicious, or just a retry loop against a stuck deploy) from
    # submitting far more tasks than this process could ever actually
    # get to, exhausting memory well before any of them would expire.
    # Matches the same NOTEBOOK_API_* convention this generated app's
    # other limits (API_KEYS, TASK_TTL_SECONDS, MAX_REQUEST_BODY_BYTES)
    # already follow.
    lines.append(
        'MAX_PENDING_TASKS = int(os.getenv('
        '"NOTEBOOK_API_MAX_TASKS", '
        '"10000"'
        '))'
    )
    lines.append(
        '# A comma-separated list, not a single value, so a key can be'
    )
    lines.append(
        '# rotated with zero downtime: add the new key alongside the old'
    )
    lines.append(
        '# one, restart, let clients switch over, then remove the old key'
    )
    lines.append(
        '# and restart again -- requests are never rejected mid-rotation.'
    )
    lines.append(
        'API_KEYS = tuple('
        'k.strip() for k in os.getenv('
        '"NOTEBOOK_API_KEY", '
        '"notebook-to-api-dev-key"'
        ').split(",") if k.strip()'
        ')'
    )
    lines.append("API_KEY_HEADER_NAME = 'X-API-Key'")
    lines.append("")
    lines.append("def _evict_expired_tasks():")
    lines.append("    # TASKS is an in-memory dict with no automatic eviction anywhere")
    lines.append("    # else in this app -- without this, a long-running deployment")
    lines.append("    # handling steady background-task traffic accumulates one entry")
    lines.append("    # per call forever, growing memory usage without bound. Called")
    lines.append("    # opportunistically on every new task's creation (lazy expiry)")
    lines.append("    # rather than a periodic background loop, so it needs no extra")
    lines.append("    # scheduler/thread and behaves the same whether or not anything")
    lines.append("    # ever polls /tasks.")
    lines.append("    now = time.time()")
    lines.append("    expired_ids = [")
    lines.append("        task_id")
    lines.append("        for task_id, task in TASKS.items()")
    lines.append("        if now - task.get('created_at', now) > TASK_TTL_SECONDS")
    lines.append("    ]")
    lines.append("    for task_id in expired_ids:")
    lines.append("        TASKS.pop(task_id, None)")
    lines.append("")
    lines.append("def verify_api_key(x_api_key: str = Header(None)):")
    lines.append("    # hmac.compare_digest instead of != : a plain string")
    lines.append("    # comparison short-circuits on the first differing byte, which")
    lines.append("    # makes response time leak how many leading characters of a")
    lines.append("    # guess were correct -- a classic timing side-channel for")
    lines.append("    # guessing the key byte by byte. Checked against every")
    lines.append("    # configured key (not just the first) so rotation doesn't")
    lines.append("    # reintroduce that leak by short-circuiting once a candidate")
    lines.append("    # key's own length/prefix happens to fail fast.")
    lines.append("    if x_api_key is None or not any(")
    lines.append("        hmac.compare_digest(x_api_key, key) for key in API_KEYS")
    lines.append("    ):")
    lines.append("        raise HTTPException(")
    lines.append("            status_code=401,")
    lines.append("            detail='Invalid API key'")
    lines.append("        )")
    lines.append("")
    lines.append("from fastapi.openapi.utils import get_openapi")
    lines.append("")
    lines.append("def custom_openapi():")
    lines.append("    if app.openapi_schema:")
    lines.append("        return app.openapi_schema")
    lines.append("")
    lines.append("    openapi_schema = get_openapi(")
    lines.append("        title=app.title,")
    lines.append("        version=app.version,")
    lines.append("        description=app.description,")
    lines.append("        routes=app.routes,")
    lines.append("    )")
    lines.append("")
    lines.append("    openapi_schema.setdefault('components', {})")
    lines.append("    openapi_schema['components'].setdefault('securitySchemes', {})")
    lines.append("")
    lines.append("    openapi_schema['components']['securitySchemes']['ApiKeyAuth'] = {")
    lines.append("        'type': 'apiKey',")
    lines.append("        'in': 'header',")
    lines.append("        'name': API_KEY_HEADER_NAME")
    lines.append("    }")
    lines.append("")
    lines.append("    app.openapi_schema = openapi_schema")
    lines.append("    return app.openapi_schema")
    lines.append("")
    lines.append("app.openapi = custom_openapi")
    lines.append("")
    lines.append("START_TIME = time.time()")
    lines.append(
        "GENERATED_AT = datetime.utcnow().isoformat() + 'Z'"
    )
    lines.append(
        "PYTHON_VERSION = sys.version.split()[0]"
    )
    lines.append("")
    protected_endpoint_count = len(functions)
    endpoint_list = [
        f"/{func['name']}"
        for func in functions
    ]
    total_generated_endpoint_count = len(endpoint_list)
    background_endpoint_count = sum(
        1
        for func in functions
        if any(
            kw in func["name"].lower()
            for kw in LONG_RUNNING_KEYWORDS
        )
    )
    lines.append("# Public infrastructure endpoints")
    lines.append("@app.get('/')")
    lines.append("def root():")
    lines.append("    return {")
    lines.append("        'service': 'Notebook-to-API Generated Service',")
    lines.append("        'generator': 'notebook-to-api',")
    lines.append("        'generator_version': '1.0.0',")
    lines.append(
        "        'generated_at': GENERATED_AT,"
    )
    lines.append(
        "        'python_version': PYTHON_VERSION,"
    )

    lines.append(
        "        'framework': 'FastAPI',"
    )
    lines.append(
        "        'background_task_support': True,"
    )

    lines.append(
        f"        'background_endpoint_count': {background_endpoint_count},"
    )
    lines.append(
        "        'available_features': ["
    )

    lines.append(
        "            'authentication',"
    )

    lines.append(
        "            'background_tasks',"
    )

    lines.append(
        "            'openapi_docs',"
    )

    lines.append(
        "            'metrics',"
    )

    lines.append(
        "            'task_monitoring',"
    )

    lines.append(
        "            'health_checks'"
    )

    lines.append(
        "        ],"
    )
    lines.append("        'documentation': {")
    lines.append("            'swagger': '/docs',")
    lines.append("            'openapi': '/openapi.json',")
    lines.append("            'redoc': '/redoc'")
    lines.append("        },")
    lines.append("        'operations': {")
    lines.append("            'health': '/health',")
    lines.append("            'ready': '/ready',")
    lines.append("            'info': '/info',")
    lines.append("            'metrics': '/metrics',")
    lines.append("            'uptime': '/uptime'")
    lines.append("        },")
    lines.append("        'task_management': {")
    lines.append("            'list': '/tasks',")
    lines.append("            'metrics': '/metrics',")
    lines.append("            'cleanup': '/tasks/cleanup',")
    lines.append("            'reset': '/tasks/reset'")
    lines.append("        },")
    lines.append("        'authentication': {")
    lines.append("            'status': '/auth/status',")
    lines.append("            'info': '/auth/info',")
    lines.append("            'validate': '/auth/validate'")
    lines.append("        },")
    lines.append(
        f"        'endpoint_count': {total_generated_endpoint_count},"
    )
    lines.append(
        f"        'protected_endpoints': {protected_endpoint_count},"
    )

    lines.append(
        f"        'sample_endpoints': {repr(endpoint_list[:10])}"
    )
    lines.append("    }")
    lines.append("")
    lines.append("@app.get('/health')")
    lines.append("def health_check():")
    lines.append("    return {'status': 'healthy'}")
    lines.append("")
    lines.append("@app.get('/ready')")
    lines.append("def readiness_check():")

    lines.append("    return {")
    lines.append("        'status': 'ready',")
    lines.append("        'tasks_registered': len(TASKS)")
    lines.append("    }")

    lines.append("")
    lines.append("@app.get('/auth/status')")
    lines.append("def auth_status():")

    lines.append("    return {")
    lines.append("        'authentication': 'enabled',")
    lines.append("        'api_key_configured': bool(API_KEYS)")
    lines.append("    }")

    lines.append("")
    lines.append("@app.get('/auth/info')")
    lines.append("def auth_info():")

    lines.append("    return {")
    lines.append("        'authentication': 'api_key',")
    lines.append("        'header': API_KEY_HEADER_NAME,")
    lines.append("        'environment_variable': 'NOTEBOOK_API_KEY',")
    lines.append("        'rate_limiting': False,")
    lines.append("        'key_rotation': True,")
    lines.append("        'configured_keys': len(API_KEYS),")
    lines.append(
        f"        'protected_endpoints': {protected_endpoint_count}"
    )
    lines.append("    }")

    lines.append("")
    lines.append("@app.get('/auth/validate')")
    lines.append("def validate_auth(_: None = Depends(verify_api_key)):")

    lines.append("    return {")
    lines.append("        'authenticated': True")
    lines.append("    }")

    lines.append("")
    lines.append("@app.get('/info')")
    lines.append("def service_info():")
    lines.append("    return {")
    lines.append('        "service": "Notebook-to-API Generated Service",')
    lines.append('        "version": "1.0.0",')
    lines.append('        "status": "running",')
    lines.append(f'        "endpoints": {repr(endpoint_list)},')
    lines.append(f'        "endpoint_count": {len(endpoint_list)},')
    lines.append(f'        "background_endpoint_count": {background_endpoint_count},')
    lines.append('        "authentication": {')
    lines.append('            "enabled": True,')
    lines.append('            "type": "api_key"')
    lines.append('        }')
    lines.append("    }")
    lines.append("")
    lines.append("@app.get('/tasks')")
    lines.append("def list_tasks(_: None = Depends(verify_api_key)):")

    lines.append("    completed_tasks = sum(")
    lines.append("        1")
    lines.append("        for task in TASKS.values()")
    lines.append("        if task.get('status') == 'completed'")
    lines.append("    )")

    lines.append("    failed_tasks = sum(")
    lines.append("        1")
    lines.append("        for task in TASKS.values()")
    lines.append("        if task.get('status') == 'failed'")
    lines.append("    )")

    lines.append("    processing_tasks = sum(")
    lines.append("        1")
    lines.append("        for task in TASKS.values()")
    lines.append("        if task.get('status') == 'processing'")
    lines.append("    )")

    lines.append("    return {")
    lines.append("        'active_tasks': len(TASKS),")
    lines.append("        'processing_tasks': processing_tasks,")
    lines.append("        'completed_tasks': completed_tasks,")
    lines.append("        'failed_tasks': failed_tasks,")
    lines.append("        'tasks': TASKS")
    lines.append("    }")
    lines.append("")
    lines.append("@app.get('/tasks/{task_id}')")
    lines.append("def get_task(task_id: str, _: None = Depends(verify_api_key)):")
    lines.append("    task = TASKS.get(task_id)")
    lines.append("")
    lines.append("    if not task:")
    lines.append("        raise HTTPException(")
    lines.append("            status_code=404,")
    lines.append("            detail=f'Task {task_id} not found'")
    lines.append("        )")
    lines.append("")
    lines.append("    return task")
    lines.append("")
    lines.append("@app.delete('/tasks/completed')")
    lines.append("def delete_completed_tasks(_: None = Depends(verify_api_key)):")

    lines.append("    completed_task_ids = [")
    lines.append("        task_id")
    lines.append("        for task_id, task in TASKS.items()")
    lines.append("        if task.get('status') == 'completed'")
    lines.append("    ]")

    lines.append("    for task_id in completed_task_ids:")
    lines.append("        TASKS.pop(task_id, None)")

    lines.append("    return {")
    lines.append("        'deleted': len(completed_task_ids),")
    lines.append("        'remaining_tasks': len(TASKS)")
    lines.append("    }")

    lines.append("")
    lines.append("@app.delete('/tasks/failed')")
    lines.append("def delete_failed_tasks(_: None = Depends(verify_api_key)):")

    lines.append("    failed_task_ids = [")
    lines.append("        task_id")
    lines.append("        for task_id, task in TASKS.items()")
    lines.append("        if task.get('status') == 'failed'")
    lines.append("    ]")

    lines.append("    for task_id in failed_task_ids:")
    lines.append("        TASKS.pop(task_id, None)")

    lines.append("    return {")
    lines.append("        'deleted': len(failed_task_ids),")
    lines.append("        'remaining_tasks': len(TASKS)")
    lines.append("    }")

    lines.append("")
    lines.append("@app.post('/tasks/cleanup')")
    lines.append("def cleanup_tasks(_: None = Depends(verify_api_key)):")

    lines.append("    completed_deleted = 0")
    lines.append("    failed_deleted = 0")

    lines.append("    task_ids = list(TASKS.keys())")

    lines.append("    for task_id in task_ids:")
    lines.append("        status = TASKS[task_id].get('status')")

    lines.append("        if status == 'completed':")
    lines.append("            TASKS.pop(task_id, None)")
    lines.append("            completed_deleted += 1")

    lines.append("        elif status == 'failed':")
    lines.append("            TASKS.pop(task_id, None)")
    lines.append("            failed_deleted += 1")

    lines.append("    return {")
    lines.append("        'completed_deleted': completed_deleted,")
    lines.append("        'failed_deleted': failed_deleted,")
    lines.append("        'remaining_tasks': len(TASKS)")
    lines.append("    }")

    lines.append("")
    lines.append("@app.get('/metrics')")
    lines.append("def metrics():")

    lines.append("    processing = sum(")
    lines.append("        1")
    lines.append("        for task in TASKS.values()")
    lines.append("        if task.get('status') == 'processing'")
    lines.append("    )")

    lines.append("    completed = sum(")
    lines.append("        1")
    lines.append("        for task in TASKS.values()")
    lines.append("        if task.get('status') == 'completed'")
    lines.append("    )")

    lines.append("    failed = sum(")
    lines.append("        1")
    lines.append("        for task in TASKS.values()")
    lines.append("        if task.get('status') == 'failed'")
    lines.append("    )")

    lines.append("    return {")
    lines.append("        'total_tasks': len(TASKS),")
    lines.append("        'processing': processing,")
    lines.append("        'completed': completed,")
    lines.append("        'failed': failed")
    lines.append("    }")

    lines.append("")
    lines.append("@app.get('/uptime')")
    lines.append("def uptime():")

    lines.append("    return {")
    lines.append("        'uptime_seconds': int(time.time() - START_TIME)")
    lines.append("    }")

    lines.append("")
    lines.append("@app.post('/tasks/reset')")
    lines.append("def reset_tasks(_: None = Depends(verify_api_key)):")

    lines.append("    deleted_tasks = len(TASKS)")

    lines.append("    TASKS.clear()")

    lines.append("    return {")
    lines.append("        'deleted_tasks': deleted_tasks")
    lines.append("    }")

    lines.append("")
    lines.append("@app.delete('/tasks/{task_id}')")
    lines.append("def delete_task(task_id: str, _: None = Depends(verify_api_key)):")

    lines.append("    if task_id not in TASKS:")
    lines.append("        raise HTTPException(")
    lines.append("            status_code=404,")
    lines.append("            detail=f'Task {task_id} not found'")
    lines.append("        )")

    lines.append("    deleted_task = TASKS.pop(task_id)")

    lines.append("    return {")
    lines.append("        'message': 'Task deleted',")
    lines.append("        'task_id': task_id,")
    lines.append("        'status': deleted_task.get('status')")
    lines.append("    }")

    lines.append("")
    lines.append("async def _run_background_task(func, task_id, *args, **kwargs):")
    lines.append("    try:")
    lines.append("        # Calling a plain (non-async) notebook function directly")
    lines.append("        # here would run its entire body synchronously, inline, on")
    lines.append("        # this coroutine -- which is this app's single asyncio")
    lines.append("        # event loop, shared by every other request the process is")
    lines.append("        # handling right now, including completely unrelated ones")
    lines.append("        # like GET /health. Confirmed against a real (non-")
    lines.append("        # TestClient) uvicorn server: a background task doing")
    lines.append("        # nothing but time.sleep(2) froze a concurrent GET /health")
    lines.append("        # for the full 2 seconds -- the exact opposite of what a")
    lines.append("        # 'background' task is supposed to mean, and especially")
    lines.append("        # damaging here since the 'train'/'process'/'generate'/")
    lines.append("        # 'embed'/'scrape' keywords that route a function to a")
    lines.append("        # background task in the first place are routinely slow,")
    lines.append("        # CPU-bound work. anyio.to_thread.run_sync (anyio is")
    lines.append("        # already a transitive dependency of fastapi/starlette --")
    lines.append("        # BackgroundTasks itself is built on it -- not a new one)")
    lines.append("        # runs a synchronous function in a worker thread instead,")
    lines.append("        # so it no longer blocks every other request this server")
    lines.append("        # is handling for its entire duration. An `async def`")
    lines.append("        # notebook function is awaited directly instead, exactly")
    lines.append("        # as before -- it already cooperates with the event loop")
    lines.append("        # on its own and has no need for a worker thread.")
    lines.append("        if inspect.iscoroutinefunction(func):")
    lines.append("            result = await func(*args, **kwargs)")
    lines.append("        else:")
    lines.append("            result = await anyio.to_thread.run_sync(")
    lines.append("                functools.partial(func, *args, **kwargs)")
    lines.append("            )")
    lines.append("        # jsonable_encoder both validates that `result` is")
    lines.append("        # actually something GET /tasks/{task_id} -- and GET")
    lines.append("        # /tasks, which returns *every* task in one response --")
    lines.append("        # can serialize, and converts common library-native")
    lines.append("        # return types (numpy arrays, pandas DataFrames, ...)")
    lines.append("        # into plain JSON-safe data first. Without this, a")
    lines.append("        # background function returning one of those -- an")
    lines.append("        # entirely ordinary thing for the 'train'/'process'/")
    lines.append("        # 'generate'/'embed'/'scrape' keywords that route a")
    lines.append("        # function here in the first place -- marked the task")
    lines.append("        # 'completed' with an unserializable result, which then")
    lines.append("        # crashed every subsequent GET /tasks/{task_id} for it,")
    lines.append("        # and GET /tasks entirely (for every task, not just this")
    lines.append("        # one), the moment FastAPI's own response serialization")
    lines.append("        # ran into it.")
    lines.append("        result = jsonable_encoder(result)")
    lines.append("        TASKS[task_id][\"status\"] = \"completed\"")
    lines.append("        TASKS[task_id][\"result\"] = result")
    lines.append("    except Exception as e:")
    lines.append("        TASKS[task_id][\"status\"] = \"failed\"")
    lines.append("        TASKS[task_id][\"error\"] = str(e)")
    lines.append("")
    # Generate Pydantic models for request bodies
    for func in functions:
        func_name = func["name"]
        model_name = model_names[func_name]
        example_payload = func.get(
            "example_payload",
            {}
        )
        lines.append(f"class {model_name}(BaseModel):")
        if not func.get("args"):
            # A zero-parameter notebook function (e.g. `def health(): ...`)
            # produces a class body with no fields and, since
            # example_payload is empty too, no model_config block either --
            # an empty class body is a SyntaxError, which would fail to
            # compile the *entire* generated app, not just this endpoint.
            lines.append("    pass")
        for arg in func.get("args", []):
            arg_name = arg.get("name", "param")
            raw_arg_type = arg.get("type")
            arg_type, _ = _resolve_annotation_source(raw_arg_type)

            # repr()'d below (see description=repr(field_description)),
            # not embedded as a raw f-string inside a hand-written
            # description="..." literal -- raw_arg_type is arbitrary,
            # notebook-author-controlled text (ast.unparse of the
            # parameter's own annotation), and can itself legitimately
            # contain a double quote (e.g. a real, valid Python
            # `Literal["a\"b"]` parameter unparses to `Literal['a"b']`).
            # Confirmed exploitable before this fix: that embedded `"`
            # closed the description="..." string literal early,
            # corrupting the rest of the line into a SyntaxError that
            # failed to compile the *entire* generated app.py, not just
            # this one field.
            field_description = (
                f"Parameter '{arg_name}' "
                f"of type {raw_arg_type or 'str'}"
            )

            default_value = arg.get("default")

            if arg.get("has_default"):
                if arg.get("default_is_literal", True):
                    default_expr = repr(default_value)
                else:
                    # A non-literal default (e.g. a notebook-defined Enum
                    # member like `Priority.HIGH` -- see
                    # extract_functions_from_code's default_is_literal in
                    # ast_parser.py). `default_value` here is raw notebook
                    # source, not a Python value, so repr()-ing it like a
                    # literal default would embed it as the *string*
                    # "Priority.HIGH" instead of the actual enum member --
                    # confirmed broken before this fix: a caller omitting
                    # this field to take its default raised an
                    # AttributeError inside the notebook's own function the
                    # moment it tried to use the (wrongly stringified)
                    # value. Reusing _resolve_annotation_source qualifies
                    # any bare notebook-defined name it references (e.g.
                    # "Priority") to notebook_module.Priority, exactly as
                    # it already does for type annotations, then the
                    # qualified source is embedded directly as a code
                    # expression rather than a string literal.
                    default_expr, _ = _resolve_annotation_source(default_value)
                lines.append(
                    f'    {arg_name}: {arg_type} = Field('
                    f'default={default_expr}, '
                    f'description={repr(field_description)}'
                    f')'
                )
            else:
                lines.append(
                    f'    {arg_name}: {arg_type} = Field('
                    f'description={repr(field_description)}'
                    f')'
                )
        if example_payload:
            lines.append("")
            lines.append("    model_config = {")
            lines.append(
                f"        'json_schema_extra': {{'example': {repr(example_payload)}}}"
            )
            lines.append("    }")
        lines.append("")
    # Generate endpoints
    for func in functions:
        func_name = func["name"]
        operation_id = func_name
        tag = "General"
        if "train" in func_name.lower():
            tag = "Training"
        elif "predict" in func_name.lower():
            tag = "Inference"
        elif any(
            kw in func_name.lower()
            for kw in ["scrape", "extract", "process"]
        ):
            tag = "Data Processing"
        elif any(
            kw in func_name.lower()
            for kw in ["embed", "vector"]
        ):
            tag = "Embeddings"
        category = tag
        args = func.get("args", [])
        example_response = func.get(
            "example_response",
            {"result": None}
        )
        return_type = func.get(
            "return_type",
            "unknown"
        )
        response_description = (
            f"Returns {return_type}"
        )
        model_name = model_names[func_name]
        call_args = ", ".join(_call_arg_expr(arg) for arg in args)
        is_background = any(kw in func_name.lower() for kw in LONG_RUNNING_KEYWORDS)
        summary = (
            func_name
            .replace("_", " ")
            .title()
        )
        # A notebook function's own docstring is exactly the description
        # its author already wrote, on purpose, for this exact function --
        # strictly more useful than a generic templated sentence that
        # doesn't even manage to say what the endpoint *does*. Before this,
        # extract_functions_from_code (parser/ast_parser.py) didn't even
        # extract it, so it was always discarded no matter what a notebook
        # author wrote; only ever falls back to the auto-generated summary
        # below for a function with no docstring at all (or one that's
        # empty/all-whitespace, which ast.get_docstring(clean=True) already
        # normalizes down to a falsy value).
        docstring = func.get("docstring")
        description = (
            docstring
            if docstring
            else (
                f"Auto-generated endpoint for {func_name}. "
                f"Operation ID: {operation_id}. "
                f"Parameters: {', '.join(arg['name'] for arg in args) if args else 'None'}."
            )
        )
        if is_background:
            # A background endpoint doesn't return `example_response`/
            # `response_description` (the notebook function's own return
            # value) at all -- it returns {"task_id": ..., "status":
            # "processing"} (see the `return` statement below) and the
            # real result only becomes available later via GET
            # /tasks/{task_id}. Documenting the function's own return
            # shape here instead was actively misleading: /docs, and any
            # third-party tool generating a client from openapi.json,
            # would expect a response this endpoint never actually sends.
            # repr()'d below for the same reason field_description is:
            # return_type is arbitrary, notebook-author-controlled text
            # (ast.unparse of the function's own return annotation) that
            # can itself legitimately contain a double quote -- embedding
            # it as a raw f-string inside a hand-written
            # "description": "..." literal let that quote close the
            # string early, corrupting the whole responses={...} dict
            # literal into a SyntaxError that failed to compile the
            # entire generated app.py.
            task_response_description = (
                f"Task enqueued. Poll GET /tasks/{{task_id}} for the "
                f"completed {return_type} result."
            )
            task_example_response = {"task_id": "<uuid>", "status": "processing"}
            lines.append(
                f'@app.post("/{func_name}", '
                f'summary="{summary}", '
                # repr()'d, not embedded as a raw f-string like the fixed,
                # server-generated boilerplate this replaces when there's
                # no docstring: description can now be a notebook author's
                # own docstring, arbitrary content that can legitimately
                # contain a double quote, a newline, or a backslash --
                # embedding it as a raw "description="..."" literal would
                # let any of those close the string early, corrupting the
                # whole @app.post(...) call into a SyntaxError and failing
                # the entire compile, not just this one endpoint's docs
                # (the exact bug class e91b1fa already fixed for the
                # Pydantic Field description and the responses={} dict's
                # own "description" entry).
                f'description={repr(description)}, '
                f'tags=["{tag}"], '
                f'operation_id="{operation_id}", '
                f'openapi_extra={{"x-notebook-to-api-category": "{category}", "x-notebook-to-api-async": True, "security": [{{"ApiKeyAuth": []}}]}}, '
                f'responses={{200: {{"description": {repr(task_response_description)}, "content": {{"application/json": {{"example": {repr(task_example_response)}}}}}}}}})'
            )
            lines.append(f"def {func_name}(req: {model_name}, background_tasks: BackgroundTasks, _: None = Depends(verify_api_key)):")
            lines.append("    _evict_expired_tasks()")
            lines.append("    if len(TASKS) >= MAX_PENDING_TASKS:")
            lines.append("        raise HTTPException(")
            lines.append("            status_code=503,")
            lines.append("            detail=(")
            lines.append("                f'Too many pending background tasks (limit '")
            lines.append("                f'{MAX_PENDING_TASKS}); try again once some have '")
            lines.append("                'finished.'")
            lines.append("            ),")
            lines.append("        )")
            lines.append("    task_id = uuid.uuid4().hex")
            lines.append("    TASKS[task_id] = {\"status\": \"processing\", \"created_at\": time.time()}")
            # Pass positional arguments to the background function
            args_expr = ", ".join(_call_arg_expr(arg) for arg in args)
            lines.append(f"    background_tasks.add_task(_run_background_task, notebook_module.{func_name}, task_id, {args_expr})")
            lines.append("    return {\"task_id\": task_id, \"status\": \"processing\"}")
        else:
            lines.append(
                f'@app.post("/{func_name}", '
                f'summary="{summary}", '
                # repr()'d, not embedded as a raw f-string like the fixed,
                # server-generated boilerplate this replaces when there's
                # no docstring: description can now be a notebook author's
                # own docstring, arbitrary content that can legitimately
                # contain a double quote, a newline, or a backslash --
                # embedding it as a raw "description="..."" literal would
                # let any of those close the string early, corrupting the
                # whole @app.post(...) call into a SyntaxError and failing
                # the entire compile, not just this one endpoint's docs
                # (the exact bug class e91b1fa already fixed for the
                # Pydantic Field description and the responses={} dict's
                # own "description" entry).
                f'description={repr(description)}, '
                f'tags=["{tag}"], '
                f'operation_id="{operation_id}", '
                f'openapi_extra={{"x-notebook-to-api-category": "{category}", "security": [{{"ApiKeyAuth": []}}]}}, '
                f'responses={{200: {{"description": {repr(response_description)}, "content": {{"application/json": {{"example": {repr(example_response)}}}}}}}}})'
            )
            is_async = func.get("is_async", False)
            def_keyword = "async def" if is_async else "def"
            call_prefix = "await " if is_async else ""
            lines.append(f"{def_keyword} {func_name}(req: {model_name}, _: None = Depends(verify_api_key)):")
            # _run_background_task already wraps a background function's own
            # call the same way (reporting the task "failed" with str(e)
            # instead of leaving it stuck "processing" forever), but a
            # synchronous endpoint had no equivalent at all: the notebook
            # function's own exception -- a ZeroDivisionError, a KeyError, a
            # bad file path, anything -- propagated straight out unhandled,
            # crashing with the exact same bare, detail-free "Internal
            # Server Error" the jsonable_encoder gap below already had.
            # HTTPException is re-raised as-is (not wrapped into a generic
            # 500) since a notebook function that imports fastapi itself and
            # deliberately raises one (e.g. HTTPException(404, ...)) is
            # already choosing its own status code and message on purpose.
            lines.append("    try:")
            lines.append(f"        result = {call_prefix}notebook_module.{func_name}({call_args})")
            lines.append("    except HTTPException:")
            lines.append("        raise")
            lines.append("    except Exception as e:")
            lines.append("        raise HTTPException(")
            lines.append("            status_code=500,")
            lines.append(
                f"            detail=f\"'{func_name}' raised "
                "{type(e).__name__}: {e}\","
            )
            lines.append("        )")
            # Same reasoning as _run_background_task's own jsonable_encoder
            # call: without pre-encoding here, a result FastAPI's response
            # serialization can't handle on its own (a raw numpy array, a
            # pandas DataFrame, ...) doesn't fail with anything resembling
            # this app's other error responses -- it crashes deep inside
            # FastAPI's routing internals as an unhandled ValueError, which
            # a real (non-test-client) deployment surfaces to the caller as
            # a bare "Internal Server Error" with no detail at all, unlike
            # every other failure mode this generated app already reports
            # clearly (auth, reserved names, oversized bodies, ...).
            lines.append("    try:")
            lines.append("        result = jsonable_encoder(result)")
            lines.append("    except Exception as e:")
            lines.append("        raise HTTPException(")
            lines.append("            status_code=500,")
            lines.append(
                f"            detail=f\"'{func_name}' returned a value that "
                "is not JSON-serializable: {e}\","
            )
            lines.append("        )")
            lines.append("    return {\"result\": result}")
        lines.append("")
    return "\n".join(lines)

# Helper to write the generated FastAPI source file
def write_generated_api(code, output_path="generated/app.py"):
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"Generated API written to: {output_path}")


def endpoint_openapi_metadata(
    schema_generator,
    endpoint
):

    description = (
        schema_generator
        .generate_openapi_description(
            endpoint
        )
    )

    return {

        "summary":
            description.summary,

        "description":
            description.description,

        "tags":
            description.tags
    }


def endpoint_examples(
    schema_generator,
    endpoint
):

    example = (
        schema_generator
        .generate_api_examples(
            endpoint
        )
    )

    return {

        "request":
            example.request_example,

        "response":
            example.response_example
    }


def endpoint_errors(
    schema_generator
):

    return (
        schema_generator
        .generate_api_error_docs()
    )

# Simple demo when run directly
if __name__ == "__main__":
    sample_functions = [
        {
            "name": "add",
            "args": [
                {"name": "a", "type": "int"},
                {"name": "b", "type": "int"}
            ],
            "return_type": "int"
        },
        {
            "name": "train_model",
            "args": [
                {"name": "epochs", "type": "int"}
            ],
            "return_type": "str"
        }
    ]
    generated_code = generate_fastapi_code(sample_functions)
    write_generated_api(generated_code)