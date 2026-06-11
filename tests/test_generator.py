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


def test_pipeline_model_generator():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineModelGenerator

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source", "config", "input_size"],
        output_fields=["result", "metric_count"],
        execution_stages=1,
        parallelism_score=1.0,
    )

    generator = PipelineModelGenerator()
    generated_code = generator.generate_request_model(spec)

    assert "class RunPipelineRequest(" in generated_code
    assert "source: str" in generated_code
    assert "config: str" in generated_code
    assert "input_size: int" in generated_code

    generated_resp = generator.generate_response_model(spec)
    assert "class RunPipelineResponse(" in generated_resp
    assert "result: str" in generated_resp
    assert "metric_count: int" in generated_resp

    from backend.generator.pipeline_route_generator import PipelineRouteGenerator
    route_gen = PipelineRouteGenerator()
    generated_route = route_gen.generate_route(spec)
    assert "response_model=\n        RunPipelineResponse" in generated_route or "response_model=RunPipelineResponse" in generated_route or "response_model=" in generated_route

    assert spec.metadata_name() == "RunPipelineMetadata"
    metadata = generator.schema_generator.generate_metadata(spec)
    assert metadata.endpoint_name == "run_pipeline"
    assert len(metadata.inputs) == 3
    assert metadata.inputs[0].name == "source"
    assert metadata.inputs[0].field_type == "str"
    assert metadata.inputs[2].name == "input_size"
    assert metadata.inputs[2].field_type == "int"
    assert len(metadata.outputs) == 2
    assert metadata.outputs[0].name == "result"
    assert metadata.outputs[1].name == "metric_count"
    assert metadata.outputs[1].field_type == "int"