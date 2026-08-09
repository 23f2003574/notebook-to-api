import ast
import builtins
import typing
from pathlib import Path

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
    needed_typing_names = set()
    for func in functions:
        for arg in func.get("args", []):
            _, typing_names = _resolve_annotation_source(arg.get("type"))
            needed_typing_names |= typing_names

    model_names = _build_model_names(functions)

    lines = []
    # Imports for the generated FastAPI app
    lines.append(
        "from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Depends"
    )
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
    # Simple in‑memory task registry used by background endpoints
    lines.append("TASKS = {}")
    lines.append(
        'API_KEY = os.getenv('
        '"NOTEBOOK_API_KEY", '
        '"notebook-to-api-dev-key"'
        ')'
    )
    lines.append("API_KEY_HEADER_NAME = 'X-API-Key'")
    lines.append("")
    lines.append("def verify_api_key(x_api_key: str = Header(None)):")
    lines.append("    # hmac.compare_digest instead of != : a plain string")
    lines.append("    # comparison short-circuits on the first differing byte, which")
    lines.append("    # makes response time leak how many leading characters of a")
    lines.append("    # guess were correct -- a classic timing side-channel for")
    lines.append("    # guessing the key byte by byte.")
    lines.append("    if x_api_key is None or not hmac.compare_digest(x_api_key, API_KEY):")
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
    lines.append("        'api_key_configured': bool(API_KEY)")
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
    lines.append("def list_tasks():")

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
    lines.append("def get_task(task_id: str):")
    lines.append("    task = TASKS.get(task_id)")
    lines.append("")
    lines.append("    if not task:")
    lines.append("        return {")
    lines.append("            'error': 'Task not found'")
    lines.append("        }")
    lines.append("")
    lines.append("    return task")
    lines.append("")
    lines.append("@app.delete('/tasks/completed')")
    lines.append("def delete_completed_tasks():")

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
    lines.append("def delete_failed_tasks():")

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
    lines.append("def cleanup_tasks():")

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
    lines.append("def reset_tasks():")

    lines.append("    deleted_tasks = len(TASKS)")

    lines.append("    TASKS.clear()")

    lines.append("    return {")
    lines.append("        'deleted_tasks': deleted_tasks")
    lines.append("    }")

    lines.append("")
    lines.append("@app.delete('/tasks/{task_id}')")
    lines.append("def delete_task(task_id: str):")

    lines.append("    if task_id not in TASKS:")
    lines.append("        return {")
    lines.append("            'error': 'Task not found'")
    lines.append("        }")

    lines.append("    deleted_task = TASKS.pop(task_id)")

    lines.append("    return {")
    lines.append("        'message': 'Task deleted',")
    lines.append("        'task_id': task_id,")
    lines.append("        'status': deleted_task.get('status')")
    lines.append("    }")

    lines.append("")
    lines.append("async def _run_background_task(func, task_id, *args, **kwargs):")
    lines.append("    try:")
    lines.append("        result = func(*args, **kwargs)")
    lines.append("        if inspect.isawaitable(result):")
    lines.append("            result = await result")
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

            field_description = (
                f"Parameter '{arg_name}' "
                f"of type {raw_arg_type or 'str'}"
            )

            default_value = arg.get("default")

            if arg.get("has_default"):
                lines.append(
                    f'    {arg_name}: {arg_type} = Field('
                    f'default={repr(default_value)}, '
                    f'description="{field_description}"'
                    f')'
                )
            else:
                lines.append(
                    f'    {arg_name}: {arg_type} = Field('
                    f'description="{field_description}"'
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
        description = (
            f"Auto-generated endpoint for {func_name}. "
            f"Operation ID: {operation_id}. "
            f"Parameters: {', '.join(arg['name'] for arg in args) if args else 'None'}."
        )
        if is_background:
            lines.append(
                f'@app.post("/{func_name}", '
                f'summary="{summary}", '
                f'description="{description}", '
                f'tags=["{tag}"], '
                f'operation_id="{operation_id}", '
                f'openapi_extra={{"x-notebook-to-api-category": "{category}", "security": [{{"ApiKeyAuth": []}}]}}, '
                f'responses={{200: {{"description": "{response_description}", "content": {{"application/json": {{"example": {repr(example_response)}}}}}}}}})'
            )
            lines.append(f"def {func_name}(req: {model_name}, background_tasks: BackgroundTasks, _: None = Depends(verify_api_key)):")
            lines.append("    task_id = uuid.uuid4().hex")
            lines.append("    TASKS[task_id] = {\"status\": \"processing\"}")
            # Pass positional arguments to the background function
            args_expr = ", ".join(_call_arg_expr(arg) for arg in args)
            lines.append(f"    background_tasks.add_task(_run_background_task, notebook_module.{func_name}, task_id, {args_expr})")
            lines.append("    return {\"task_id\": task_id, \"status\": \"processing\"}")
        else:
            lines.append(
                f'@app.post("/{func_name}", '
                f'summary="{summary}", '
                f'description="{description}", '
                f'tags=["{tag}"], '
                f'operation_id="{operation_id}", '
                f'openapi_extra={{"x-notebook-to-api-category": "{category}", "security": [{{"ApiKeyAuth": []}}]}}, '
                f'responses={{200: {{"description": "{response_description}", "content": {{"application/json": {{"example": {repr(example_response)}}}}}}}}})'
            )
            is_async = func.get("is_async", False)
            def_keyword = "async def" if is_async else "def"
            call_prefix = "await " if is_async else ""
            lines.append(f"{def_keyword} {func_name}(req: {model_name}, _: None = Depends(verify_api_key)):")
            lines.append(f"    result = {call_prefix}notebook_module.{func_name}({call_args})")
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