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
    lines.append("from pydantic import BaseModel")
    lines.append("import generated.runtime.notebook_module as notebook_module")
    lines.append("")
    lines.append("app = FastAPI()")
    lines.append("")
    # Simple in‑memory task registry used by background endpoints
    lines.append("TASKS = {}")
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
        lines.append(f"class {model_name}(BaseModel):")
        for arg in func.get("args", []):
            arg_name = arg.get("name", "param")
            arg_type = arg.get("type", "str")
            lines.append(f"    {arg_name}: {arg_type}")
        lines.append("")
    # Generate endpoints
    for func in functions:
        func_name = func["name"]
        args = func.get("args", [])
        model_name = f"{func_name[0].upper()}{func_name[1:]}Request"
        call_args = ", ".join(f"req.{arg['name']}" for arg in args)
        is_background = any(kw in func_name.lower() for kw in LONG_RUNNING_KEYWORDS)
        description = (
            f"Auto-generated endpoint for {func_name}. "
            f"Parameters: {', '.join(arg['name'] for arg in args) if args else 'None'}."
        )
        if is_background:
            lines.append(f'@app.post("/{func_name}", description="{description}")')
            lines.append(f"def {func_name}(req: {model_name}, background_tasks: BackgroundTasks):")
            lines.append("    task_id = uuid.uuid4().hex")
            lines.append("    TASKS[task_id] = {\"status\": \"processing\"}")
            # Pass positional arguments to the background function
            args_expr = ", ".join(f"req.{arg['name']}" for arg in args)
            lines.append(f"    background_tasks.add_task(_run_background_task, notebook_module.{func_name}, task_id, {args_expr})")
            lines.append("    return {\"task_id\": task_id, \"status\": \"processing\"}")
        else:
            lines.append(f'@app.post("/{func_name}", description="{description}")')
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