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
    assert metadata.input_count() == 3
    assert metadata.output_count() == 2
    assert len(metadata.all_fields()) == 5

    openapi_schema = generator.schema_generator.generate_openapi_schema(spec)
    assert openapi_schema["endpoint"] == "run_pipeline"
    assert openapi_schema["request"]["source"] == {"type": "str"}
    assert openapi_schema["request"]["input_size"] == {"type": "int"}
    assert openapi_schema["response"]["result"] == {"type": "str"}
    assert openapi_schema["response"]["metric_count"] == {"type": "int"}

    sdk_types = generator.schema_generator.generate_sdk_types(spec)
    assert sdk_types["request_types"]["source"] == "str"
    assert sdk_types["request_types"]["input_size"] == "int"
    assert sdk_types["response_types"]["result"] == "str"
    assert sdk_types["response_types"]["metric_count"] == "int"

    assert spec.typescript_request_name() == "RunPipelineRequest"
    assert spec.typescript_response_name() == "RunPipelineResponse"

    ts_interfaces = generator.schema_generator.generate_typescript_interfaces(spec)
    assert "export interface RunPipelineRequest {" in ts_interfaces["request"]
    assert "source: string;" in ts_interfaces["request"]
    assert "input_size: number;" in ts_interfaces["request"]
    assert "export interface RunPipelineResponse {" in ts_interfaces["response"]
    assert "result: string;" in ts_interfaces["response"]
    assert "metric_count: number;" in ts_interfaces["response"]

    assert spec.client_method_name() == "run_pipeline"
    ts_client = generator.schema_generator.generate_typescript_client(spec)
    assert "export async function run_pipeline(" in ts_client
    assert "request: RunPipelineRequest" in ts_client
    assert "Promise<RunPipelineResponse>" in ts_client
    assert '"/run_pipeline"' in ts_client

    assert spec.sdk_module_name() == "run_pipeline_sdk"
    assert spec.sdk_filename() == "run_pipeline_sdk.ts"
    ts_sdk = generator.schema_generator.generate_typescript_sdk(spec)
    assert "export interface RunPipelineRequest {" in ts_sdk
    assert "export interface RunPipelineResponse {" in ts_sdk
    assert "export async function run_pipeline(" in ts_sdk

    sdk_index = generator.schema_generator.generate_sdk_index([spec])
    assert 'export * from "./run_pipeline_sdk";' in sdk_index

    assert spec.npm_package_name() == "run-pipeline-sdk"
    sdk_package = generator.schema_generator.generate_sdk_package(spec.npm_package_name())
    assert '"name": "run-pipeline-sdk"' in sdk_package["package_json"]
    assert '"compilerOptions": {' in sdk_package["tsconfig"]


def test_pipeline_contract_validator():
    import pytest
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineContractValidator

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )

    validator = PipelineContractValidator()

    # Valid schema
    valid_schema = {
        "request": {"source": {"type": "str"}},
        "response": {"result": {"type": "str"}}
    }
    assert validator.validate_schema(spec, valid_schema) is True

    # Invalid request schema
    invalid_req_schema = {
        "request": {"mismatch": {"type": "str"}},
        "response": {"result": {"type": "str"}}
    }
    with pytest.raises(ValueError, match="Request schema does not match endpoint spec"):
        validator.validate_schema(spec, invalid_req_schema)

    # Invalid response schema
    invalid_resp_schema = {
        "request": {"source": {"type": "str"}},
        "response": {"mismatch": {"type": "str"}}
    }
    with pytest.raises(ValueError, match="Response schema does not match endpoint spec"):
        validator.validate_schema(spec, invalid_resp_schema)