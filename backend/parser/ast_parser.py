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
                "example_payload": generate_example_payload(args),
                "example_response": generate_example_response(
                    return_type
                )
            }

            functions.append(function_info)

    return functions


def normalize_type_annotation(arg_type):
    if not arg_type:
        return arg_type

    if arg_type.startswith("Annotated["):
        return (
            arg_type
            .replace("Annotated[", "")
            .rstrip("]")
            .split(",")[0]
            .strip()
        )

    if arg_type.startswith("Optional["):
        return (
            arg_type
            .replace("Optional[", "")
            .replace("]", "")
        )

    if arg_type.startswith("Union["):
        return (
            arg_type
            .replace("Union[", "")
            .replace("]", "")
            .split(",")[0]
            .strip()
        )

    if "|" in arg_type:
        return arg_type.split("|")[0].strip()

    if (
        arg_type.startswith("List[")
        or arg_type.startswith("list[")
    ):
        return "list"

    if (
        arg_type.startswith("Dict[")
        or arg_type.startswith("dict[")
    ):
        return "dict"

    if (
        arg_type.startswith("Tuple[")
        or arg_type.startswith("tuple[")
    ):
        return "tuple"

    if (
        arg_type.startswith("Set[")
        or arg_type.startswith("set[")
    ):
        return "set"

    return arg_type


def generate_example_response(return_type):
    if not return_type:
        return {
            "result": None
        }

    return_type = normalize_type_annotation(
        return_type
    )

    type_defaults = {
        "int": 0,
        "float": 0.0,
        "str": "",
        "bool": False,
        "list": [],
        "dict": {},
        "tuple": [],
        "set": []
    }

    if return_type in (
        "pd.DataFrame",
        "DataFrame",
        "pd.Series",
        "Series",
        "np.ndarray",
        "ndarray"
    ):
        return {
            "result": []
        }

    return {
        "result": type_defaults.get(
            return_type,
            None
        )
    }


def generate_example_payload(args):
    payload = {}

    type_defaults = {
        "int": 0,
        "float": 0.0,
        "str": "",
        "bool": False,
        "list": [],
        "dict": {},
        "tuple": [],
        "set": []
    }

    for arg in args:
        arg_name = arg.get("name")
        arg_type = normalize_type_annotation(
            arg.get("type")
        )

        if arg_type and arg_type.startswith("Literal["):
            literal_values = (
                arg_type
                .replace("Literal[", "")
                .rstrip("]")
                .split(",")
            )
            first_value = literal_values[0].strip()
            if (
                first_value.startswith('"')
                and first_value.endswith('"')
            ):
                first_value = first_value[1:-1]
            elif (
                first_value.startswith("'")
                and first_value.endswith("'")
            ):
                first_value = first_value[1:-1]
            payload[arg_name] = first_value
            continue

        if arg_type in (
            "pd.DataFrame",
            "DataFrame"
        ):
            payload[arg_name] = []
            continue

        if arg_type in (
            "np.ndarray",
            "ndarray"
        ):
            payload[arg_name] = []
            continue

        if arg_type in (
            "pd.Series",
            "Series"
        ):
            payload[arg_name] = []
            continue

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
from typing import Optional, Union, Literal, Annotated, List, Dict
import pandas as pd
import numpy as np

def add(a: int, b: int) -> int:
    return a + b

def greet(name: Optional[str]) -> str:
    return f"Hello {name}"

def parse(value: Union[int, str]) -> str:
    return str(value)

def identify(user_id: int | str) -> str:
    return str(user_id)

def train(model: Literal["xgboost", "rf"]) -> str:
    return model

def search(query: Annotated[str, "search query"]) -> list:
    return []

def classify(labels: List[str]) -> str:
    return labels[0]

def predict(data: pd.DataFrame) -> list:
    return []

def embed(arr: np.ndarray) -> list:
    return []

def fit(epochs=100, lr=0.001) -> str:
    return "done"
"""

    extracted = extract_functions_from_code(sample_code)
    for func in extracted:
        print("Function:", func)

    imports = extract_imports_from_code(sample_code)
    print("Imports:", imports)