from pathlib import Path


def generate_fastapi_code(functions):
    lines = []

    lines.append("from fastapi import FastAPI")
    lines.append("")
    lines.append("app = FastAPI()")
    lines.append("")

    for func in functions:
        func_name = func["name"]
        args = func["args"]

        params = ", ".join(args)

        endpoint = f"""
@app.get("/{func_name}")
def {func_name}({params}):
    return {{
        "message": "Function {func_name} called successfully"
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
            "args": ["a", "b"]
        }
    ]

    generated_code = generate_fastapi_code(sample_functions)

    write_generated_api(generated_code)

