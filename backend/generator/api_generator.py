from pathlib import Path


def generate_fastapi_code(functions):
    lines = []

    lines.append("from fastapi import FastAPI")
    lines.append("from pydantic import BaseModel")
    lines.append("import generated.runtime.notebook_module as notebook_module")
    lines.append("")
    lines.append("app = FastAPI()")
    lines.append("")

    # Generate Pydantic models first
    for func in functions:
        func_name = func["name"]
        model_name = f"{func_name[0].upper()}{func_name[1:]}Request"

        lines.append(f"class {model_name}(BaseModel):")
        for arg in func["args"]:
            arg_name = arg["name"]
            arg_type = arg["type"] or "str"
            lines.append(f"    {arg_name}: {arg_type}")
        lines.append("")

    # Generate POST endpoints
    for func in functions:
        func_name = func["name"]
        args = func["args"]
        model_name = f"{func_name[0].upper()}{func_name[1:]}Request"

        call_args = ", ".join(f"req.{arg['name']}" for arg in args)

        endpoint = f"""
@app.post("/{func_name}")
def {func_name}(req: {model_name}):
    result = notebook_module.{func_name}({call_args})

    return {{
        "result": result
    }}
"""
        lines.append(endpoint)

    return "\n".join(lines)


def write_generated_api(code, output_path="generated/app.py"):
    output_file = Path(output_path)

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(code)

    print(f"Generated API written to: {output_path}")


if __name__ == "__main__":
    sample_functions = [
        {
            "name": "add",
            "args": [
                {"name": "a", "type": "int"},
                {"name": "b", "type": "int"}
            ],
            "return_type": "int"
        }
    ]

    generated_code = generate_fastapi_code(sample_functions)

    write_generated_api(generated_code)

