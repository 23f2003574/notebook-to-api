from pathlib import Path

# Keywords indicating a function should be run as a background task
LONG_RUNNING_KEYWORDS = [
    "train",
    "process",
    "generate",
    "embed",
    "scrape",
]

# Template for generating the FastAPI application source code
def generate_fastapi_code(functions):
    """Generate FastAPI app code for the given functions.

    Each function is examined; if its name contains any of the
    LONG_RUNNING_KEYWORDS, an endpoint is created that enqueues the
    function as a BackgroundTask and returns a task_id. Otherwise a
    regular synchronous endpoint is generated.
    """
    lines = []
    # Imports for the generated FastAPI app
    lines.append("from fastapi import FastAPI, BackgroundTasks")
    lines.append("import uuid")
    lines.append("from pydantic import BaseModel, Field")
    lines.append("import generated.runtime.notebook_module as notebook_module")
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
    # Simple in‑memory task registry used by background endpoints
    lines.append("TASKS = {}")
    lines.append("")
    lines.append("@app.get('/health')")
    lines.append("def health_check():")
    lines.append("    return {'status': 'healthy'}")
    lines.append("")
    endpoint_list = [
        f"/{func['name']}"
        for func in functions
    ]
    background_endpoint_count = sum(
        1
        for func in functions
        if any(
            kw in func["name"].lower()
            for kw in LONG_RUNNING_KEYWORDS
        )
    )
    lines.append("@app.get('/info')")
    lines.append("def service_info():")
    lines.append("    return {")
    lines.append('        "service": "Notebook-to-API Generated Service",')
    lines.append('        "version": "1.0.0",')
    lines.append('        "status": "running",')
    lines.append(
        f'        "endpoints": {repr(endpoint_list)},'
    )
    lines.append(
        f'        "endpoint_count": {len(endpoint_list)},'
    )
    lines.append(
        f'        "background_endpoint_count": {background_endpoint_count}'
    )
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
    lines.append("def _run_background_task(func, task_id, *args, **kwargs):")
    lines.append("    try:")
    lines.append("        result = func(*args, **kwargs)")
    lines.append("        TASKS[task_id][\"status\"] = \"completed\"")
    lines.append("        TASKS[task_id][\"result\"] = result")
    lines.append("    except Exception as e:")
    lines.append("        TASKS[task_id][\"status\"] = \"failed\"")
    lines.append("        TASKS[task_id][\"error\"] = str(e)")
    lines.append("")
    # Generate Pydantic models for request bodies
    for func in functions:
        func_name = func["name"]
        model_name = f"{func_name[0].upper()}{func_name[1:]}Request"
        example_payload = func.get(
            "example_payload",
            {}
        )
        lines.append(f"class {model_name}(BaseModel):")
        for arg in func.get("args", []):
            arg_name = arg.get("name", "param")
            arg_type = arg.get("type", "str")

            field_description = (
                f"Parameter '{arg_name}' "
                f"of type {arg_type}"
            )

            default_value = arg.get("default")

            if default_value is not None:
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
        model_name = f"{func_name[0].upper()}{func_name[1:]}Request"
        call_args = ", ".join(f"req.{arg['name']}" for arg in args)
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
                f'openapi_extra={{"x-notebook-to-api-category": "{category}"}}, '
                f'responses={{200: {{"description": "{response_description}", "content": {{"application/json": {{"example": {repr(example_response)}}}}}}}}})'
            )
            lines.append(f"def {func_name}(req: {model_name}, background_tasks: BackgroundTasks):")
            lines.append("    task_id = uuid.uuid4().hex")
            lines.append("    TASKS[task_id] = {\"status\": \"processing\"}")
            # Pass positional arguments to the background function
            args_expr = ", ".join(f"req.{arg['name']}" for arg in args)
            lines.append(f"    background_tasks.add_task(_run_background_task, notebook_module.{func_name}, task_id, {args_expr})")
            lines.append("    return {\"task_id\": task_id, \"status\": \"processing\"}")
        else:
            lines.append(
                f'@app.post("/{func_name}", '
                f'summary="{summary}", '
                f'description="{description}", '
                f'tags=["{tag}"], '
                f'operation_id="{operation_id}", '
                f'openapi_extra={{"x-notebook-to-api-category": "{category}"}}, '
                f'responses={{200: {{"description": "{response_description}", "content": {{"application/json": {{"example": {repr(example_response)}}}}}}}}})'
            )
            lines.append(f"def {func_name}(req: {model_name}):")
            lines.append(f"    result = notebook_module.{func_name}({call_args})")
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