import ast


def extract_functions_from_code(code):
    tree = ast.parse(code)

    functions = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            function_info = {
                "name": node.name,
                "args": [arg.arg for arg in node.args.args]
            }

            functions.append(function_info)

    return functions


if __name__ == "__main__":
    sample_code = """
def add(a, b):
    return a + b

def greet(name):
    return f"Hello {name}"
"""

    extracted = extract_functions_from_code(sample_code)

    for func in extracted:
        print(func)
