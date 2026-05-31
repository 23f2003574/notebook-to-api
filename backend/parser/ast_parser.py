import ast


def extract_functions_from_code(code):
    tree = ast.parse(code)

    functions = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = []

            defaults = node.args.defaults

            default_offset = (
                len(node.args.args)
                - len(defaults)
            )

            for idx, arg in enumerate(node.args.args):
                arg_info = {
                    "name": arg.arg,
                    "type": None,
                    "default": None
                }

                if arg.annotation:
                    arg_info["type"] = ast.unparse(arg.annotation)

                default_index = idx - default_offset

                if default_index >= 0:
                    try:
                        arg_info["default"] = ast.literal_eval(
                            defaults[default_index]
                        )
                    except Exception:
                        arg_info["default"] = ast.unparse(
                            defaults[default_index]
                        )

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

        if arg.get("default") is not None:
            payload[arg_name] = arg["default"]
        else:
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

def predict(model="xgboost") -> str:
    return model

def train(epochs=100, lr=0.001) -> str:
    return "done"
"""

    extracted = extract_functions_from_code(sample_code)
    for func in extracted:
        print("Function:", func)

    imports = extract_imports_from_code(sample_code)
    print("Imports:", imports)