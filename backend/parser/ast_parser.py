import ast


def extract_functions_from_code(code):
    tree = ast.parse(code)

    functions = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = []

            for arg in node.args.args:
                arg_info = {
                    "name": arg.arg,
                    "type": None
                }

                if arg.annotation:
                    arg_info["type"] = ast.unparse(arg.annotation)

                args.append(arg_info)

            return_type = None
            if node.returns:
                return_type = ast.unparse(node.returns)

            function_info = {
                "name": node.name,
                "args": args,
                "return_type": return_type,
                "example_payload": generate_example_payload(args)
            }

            functions.append(function_info)

    return functions


def generate_example_payload(args):
    payload = {}

    type_defaults = {
        "int": 0,
        "float": 0.0,
        "str": "",
        "bool": False,
        "list": [],
        "dict": {}
    }

    for arg in args:
        arg_name = arg.get("name")

        arg_type = arg.get("type")

        payload[arg_name] = type_defaults.get(
            arg_type,
            None
        )

    return payload


def extract_imports_from_code(code):
    tree = ast.parse(code)

    imports = set()

    # Map common Python import modules to their PyPI package names
    pypi_mapping = {
        "sklearn": "scikit-learn",
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "yaml": "PyYAML",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base_module = alias.name.split(".")[0]
                imports.add(pypi_mapping.get(base_module, base_module))

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base_module = node.module.split(".")[0]
                imports.add(pypi_mapping.get(base_module, base_module))

    return imports


if __name__ == "__main__":
    sample_code = """
import sys
import os
import pandas as pd
from sklearn.linear_model import LinearRegression

def add(a: int, b: int) -> int:
    return a + b

def greet(name: str) -> str:
    return f"Hello {name}"
"""

    extracted = extract_functions_from_code(sample_code)
    for func in extracted:
        print("Function:", func)

    imports = extract_imports_from_code(sample_code)
    print("Imports:", imports)