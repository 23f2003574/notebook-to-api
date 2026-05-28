from backend.generator.api_generator import (
    generate_fastapi_code
)


def test_api_generation():

    functions = [
        {
            "name": "add",
            "args": [
                {
                    "name": "a",
                    "type": "int"
                },
                {
                    "name": "b",
                    "type": "int"
                }
            ],
            "return_type": "int"
        }
    ]

    code = generate_fastapi_code(functions)

    assert "@app.post" in code


def test_route_generation():

    functions = [
        {
            "name": "predict",
            "args": [],
            "return_type": None
        }
    ]

    code = generate_fastapi_code(functions)

    assert "/predict" in code


def test_pydantic_model_generation():

    functions = [
        {
            "name": "train_model",
            "args": [
                {
                    "name": "epochs",
                    "type": "int"
                }
            ],
            "return_type": None
        }
    ]

    code = generate_fastapi_code(functions)

    assert "BaseModel" in code