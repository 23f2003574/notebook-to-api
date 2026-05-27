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
                "return_type": return_type
            }

            functions.append(function_info)

    return functions


if __name__ == "__main__":
    sample_code = """
def add(a: int, b: int) -> int:
    return a + b

def greet(name: str) -> str:
    return f"Hello {name}"
"""

    extracted = extract_functions_from_code(sample_code)

    for func in extracted:
        print(func)
