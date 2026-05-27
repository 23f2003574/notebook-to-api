from pathlib import Path


def generate_fastapi_code(functions):
    lines = []

    lines.append("from fastapi import FastAPI")
    lines.append("import generated.runtime.notebook_module as notebook_module")
    lines.append("")
    lines.append("app = FastAPI()")
    lines.append("")
    lines.append("def _parse_val(val):")
    lines.append("    if val is None:")
    lines.append("        return val")
    lines.append("    try:")
    lines.append("        return int(val)")
    lines.append("    except ValueError:")
    lines.append("        pass")
    lines.append("    try:")
    lines.append("        return float(val)")
    lines.append("    except ValueError:")
    lines.append("        pass")
    lines.append("    return val")
    lines.append("")

    for func in functions:
        func_name = func["name"]
        args = func["args"]

        params = ", ".join(args)
        call_params = ", ".join(f"_parse_val({arg})" for arg in args)

        endpoint = f"""
@app.get("/{func_name}")
def {func_name}({params}):
    result = notebook_module.{func_name}({call_params})

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
            "args": ["a", "b"]
        }
    ]

    generated_code = generate_fastapi_code(sample_functions)

    write_generated_api(generated_code)

