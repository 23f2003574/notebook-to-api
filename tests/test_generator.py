import sys
import types

import pytest

from backend.generator.api_generator import (
    generate_fastapi_code,
    ReservedFunctionNameError,
)


def _register_fake_notebook_module(monkeypatch, package_name="generated"):
    """Generated code always contains a real
    `import <package_name>.runtime.notebook_module as notebook_module`
    statement (see api_generator.py). A plain `namespace = {"notebook_module":
    ...}` dict passed to exec() does NOT satisfy that -- `import X as Y`
    always performs a real import of X via sys.modules/sys.path and
    ignores whatever's already bound to the name Y, so exec()ing generated
    code without actually registering these modules only "works" by
    accident if a real `<package_name>/runtime/notebook_module.py`
    happens to already exist somewhere importable (e.g. a stray leftover
    `generated/` directory from a previous local run) -- which silently
    passes locally but fails with ModuleNotFoundError in a clean checkout.
    """
    parent = types.ModuleType(package_name)
    runtime_pkg = types.ModuleType(f"{package_name}.runtime")
    notebook_module = types.ModuleType(f"{package_name}.runtime.notebook_module")

    monkeypatch.setitem(sys.modules, package_name, parent)
    monkeypatch.setitem(sys.modules, f"{package_name}.runtime", runtime_pkg)
    monkeypatch.setitem(
        sys.modules, f"{package_name}.runtime.notebook_module", notebook_module
    )

    return notebook_module


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


def test_api_key_check_uses_constant_time_comparison():
    """A plain `x_api_key != API_KEY` short-circuits on the first
    differing byte, leaking via response timing how many leading
    characters of a guess were correct -- a classic timing side-channel
    for guessing the key byte by byte. Must use hmac.compare_digest.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "import hmac" in code
    assert "hmac.compare_digest(x_api_key, key) for key in API_KEYS" in code
    assert "x_api_key != " not in code
    assert "x_api_key in API_KEYS" not in code


def test_api_key_check_still_rejects_missing_header():
    """hmac.compare_digest raises TypeError on None, so the missing-header
    case (x_api_key defaults to None) must be checked before calling it,
    not delegated to it.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "if x_api_key is None or not any(" in code


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


def test_async_function_generates_awaited_async_endpoint():

    functions = [
        {
            "name": "fetch_data",
            "args": [{"name": "url", "type": "str"}],
            "return_type": "dict",
            "is_async": True,
        }
    ]

    code = generate_fastapi_code(functions)

    assert "async def fetch_data(" in code
    assert "await notebook_module.fetch_data(" in code


def test_sync_function_generates_unawaited_sync_endpoint():

    functions = [
        {
            "name": "add",
            "args": [{"name": "a", "type": "int"}],
            "return_type": "int",
            "is_async": False,
        }
    ]

    code = generate_fastapi_code(functions)

    assert "def add(" in code
    assert "async def add(" not in code
    assert "await notebook_module.add(" not in code
    assert "result = notebook_module.add(" in code


def test_keyword_only_arg_is_passed_by_keyword_in_generated_call():

    functions = [
        {
            # Deliberately not a LONG_RUNNING_KEYWORDS name, so this takes
            # the direct-call endpoint path rather than the background-task
            # path (which forwards args differently, through add_task).
            "name": "score",
            "args": [
                {"name": "data", "type": "list", "kind": "positional"},
                {"name": "epochs", "type": "int", "default": 10, "has_default": True, "kind": "keyword_only"},
            ],
            "return_type": "dict",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "notebook_module.score(req.data, epochs=req.epochs)" in code


def test_tasks_endpoints_require_api_key_auth():
    """Confirmed exploitable before this fix: the /tasks family of
    endpoints (which return stored function call inputs/outputs, or let
    a caller wipe task state) omitted Depends(verify_api_key) even though
    every per-function endpoint and /auth/validate require it -- anyone
    could read past task results or delete all task state with no
    credentials at all.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    assert "def list_tasks(_: None = Depends(verify_api_key)):" in code
    assert "def get_task(task_id: str, _: None = Depends(verify_api_key)):" in code
    assert "def delete_completed_tasks(_: None = Depends(verify_api_key)):" in code
    assert "def delete_failed_tasks(_: None = Depends(verify_api_key)):" in code
    assert "def cleanup_tasks(_: None = Depends(verify_api_key)):" in code
    assert "def reset_tasks(_: None = Depends(verify_api_key)):" in code
    assert "def delete_task(task_id: str, _: None = Depends(verify_api_key)):" in code


def test_background_task_creation_evicts_expired_tasks_and_stamps_created_at():
    """Confirmed exploitable before this fix: TASKS is an in-memory dict
    with no automatic eviction anywhere in the generated app -- nothing
    calls the manual /tasks/cleanup-style endpoints on its own, so a
    long-running deployment handling steady background-task traffic
    accumulates one entry per call forever. A new task's creation must
    both stamp a created_at timestamp (needed to determine expiry) and
    sweep out anything already past TASK_TTL_SECONDS.
    """

    functions = [
        {"name": "process_data", "args": [], "return_type": "dict"},
    ]

    code = generate_fastapi_code(functions)

    assert "TASK_TTL_SECONDS = int(os.getenv(" in code
    assert '"created_at": time.time()' in code
    assert "_evict_expired_tasks()" in code
    # Eviction must run before the new task is recorded, not after --
    # otherwise the brand new task could itself be swept if TTL is 0.
    assert code.index("_evict_expired_tasks()") < code.index('TASKS[task_id] = {"status": "processing"')


def test_background_endpoint_documents_the_task_response_it_actually_sends():
    """Confirmed wrong before this fix: a background endpoint's decorator
    documented `example_response`/the function's own return type (e.g.
    {"result": ""}) as its 200 response -- but the function body actually
    always `return`s {"task_id": ..., "status": "processing"} instead,
    with the real result only available later via GET /tasks/{task_id}.
    /docs, and any third-party tool generating a client from
    openapi.json, would be told to expect a response this endpoint never
    sends.
    """

    functions = [
        {
            "name": "train_model",
            "args": [],
            "return_type": "str",
            "example_response": {"result": "trained"},
        },
    ]

    code = generate_fastapi_code(functions)

    decorator_line = next(
        line for line in code.splitlines() if '@app.post("/train_model"' in line
    )

    assert "'task_id': '<uuid>'" in decorator_line
    assert "'status': 'processing'" in decorator_line
    assert "trained" not in decorator_line
    assert '"x-notebook-to-api-async": True' in decorator_line


def test_non_background_endpoint_is_not_marked_async_and_documents_its_own_result():

    functions = [
        {
            "name": "add",
            "args": [],
            "return_type": "int",
            "example_response": {"result": 3},
        },
    ]

    code = generate_fastapi_code(functions)

    decorator_line = next(
        line for line in code.splitlines() if '@app.post("/add"' in line
    )

    assert "x-notebook-to-api-async" not in decorator_line
    assert "'result': 3" in decorator_line


def test_keyword_only_arg_forwarded_by_keyword_through_background_task():

    functions = [
        {
            "name": "train",
            "args": [
                {"name": "data", "type": "list", "kind": "positional"},
                {"name": "epochs", "type": "int", "default": 10, "has_default": True, "kind": "keyword_only"},
            ],
            "return_type": "dict",
        }
    ]

    code = generate_fastapi_code(functions)

    assert (
        "background_tasks.add_task(_run_background_task, notebook_module.train, "
        "task_id, req.data, epochs=req.epochs)"
    ) in code


def test_field_with_explicit_none_default_is_not_required():
    """A default of None (has_default=True, default=None) must produce an
    optional Pydantic field, not a required one -- otherwise the generated
    endpoint 422s on any call that omits the field, even though the
    underlying notebook function has a perfectly valid default.
    """

    functions = [
        {
            "name": "greet",
            "args": [
                {"name": "name", "type": "str", "has_default": False, "kind": "positional"},
                {"name": "title", "type": "str", "default": None, "has_default": True, "kind": "positional"},
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "title: str = Field(default=None," in code
    assert "name: str = Field(description=" in code
    assert "name: str = Field(default=" not in code


def test_field_with_no_default_is_required():

    functions = [
        {
            "name": "greet",
            "args": [
                {"name": "name", "type": "str", "has_default": False, "kind": "positional"},
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "name: str = Field(description=" in code
    assert "default=" not in code.split("class GreetRequest(BaseModel):")[1].split("\n\n")[0]


def test_typing_generic_argument_types_get_a_matching_typing_import(monkeypatch):
    """Confirmed exploitable before this fix: arg["type"] (a raw
    ast.unparse'd annotation like "List[float]" or "Optional[str]") was
    written straight into the generated Pydantic model with no matching
    `from typing import ...`, so building the model at runtime raised
    `PydanticUserError: 'PredictRequest' is not fully defined; you should
    define 'List', then call 'PredictRequest.model_rebuild()'` the first
    time FastAPI needed the schema (i.e. on the first request or /docs
    load, not at compile time).
    """

    functions = [
        {
            "name": "predict",
            "args": [
                {"name": "items", "type": "List[float]", "has_default": False, "kind": "positional"},
                {"name": "name", "type": "Optional[str]", "default": None, "has_default": True, "kind": "positional"},
                {"name": "meta", "type": "Dict[str, Any]", "default": None, "has_default": True, "kind": "positional"},
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "from typing import Any, Dict, List, Optional" in code
    assert "items: List[float] = Field(" in code
    assert "name: Optional[str] = Field(" in code
    assert "meta: Dict[str, Any] = Field(" in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["PredictRequest"].model_json_schema()
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["properties"]["name"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_untyped_argument_defaults_to_str_not_the_literal_none_type():
    """Confirmed exploitable before this fix: arg.get("type", "str") only
    falls back to "str" when the "type" key is *absent*, but the parser
    always sets it (to None when there's no annotation), so an untyped
    notebook parameter produced a field literally annotated `: None`,
    rejecting every value including its own default.
    """

    functions = [
        {
            "name": "greet",
            "args": [
                {"name": "name", "type": None, "has_default": True, "default": "world", "kind": "positional"},
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "name: str = Field(" in code
    assert ": None = Field(" not in code


def test_notebook_defined_type_is_qualified_with_notebook_module():
    """A bare class/Enum name from the notebook (e.g. a Status Enum used as
    a parameter type) isn't defined anywhere in the generated app's own
    namespace, so referencing it unqualified raises a NameError while
    building the model. It must be qualified as `notebook_module.<name>`,
    the alias the generated app already imports the notebook's runtime
    module under.
    """

    functions = [
        {
            "name": "set_status",
            "args": [
                {"name": "status", "type": "Status", "has_default": False, "kind": "positional"},
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "status: notebook_module.Status = Field(" in code
    assert "status: Status = Field(" not in code
    # The human-readable Field description should stay unqualified.
    assert "of type Status" in code


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


def test_zero_argument_function_produces_a_valid_request_model():
    """Confirmed exploitable before this fix: a zero-parameter notebook
    function (e.g. `def health(): ...`) produced `class HealthRequest
    (BaseModel):` with no fields and no model_config -- an empty class
    body, which is a SyntaxError that fails to compile the *entire*
    generated app, not just this one endpoint.
    """

    functions = [
        {"name": "get_status", "args": [], "return_type": "dict"},
    ]

    code = generate_fastapi_code(functions)

    compile(code, "<generated>", "exec")
    assert "class Get_statusRequest(BaseModel):\n    pass" in code


def test_notebook_function_named_verify_api_key_is_rejected():
    """Confirmed exploitable before this fix: a notebook function named
    verify_api_key was emitted as `def verify_api_key(...)`, rebinding the
    module-level name the real auth check is defined under. Since
    Depends(verify_api_key) defaults are resolved at def-statement
    execution time (top-to-bottom module load), every endpoint defined
    *after* the collision silently got Depends(verify_api_key) pointing
    at the notebook's own function instead of the real guard -- disabling
    API-key authentication for the rest of the app with no error.
    """

    functions = [
        {"name": "verify_api_key", "args": [], "return_type": "dict"},
    ]

    with pytest.raises(ReservedFunctionNameError, match="verify_api_key"):
        generate_fastapi_code(functions)


def test_notebook_function_named_after_other_reserved_infrastructure_is_rejected():

    for reserved_name in ["custom_openapi", "root", "health_check", "notebook_module", "TASKS"]:
        functions = [
            {"name": reserved_name, "args": [], "return_type": "dict"},
        ]

        with pytest.raises(ReservedFunctionNameError):
            generate_fastapi_code(functions)


def test_non_colliding_functions_alongside_a_reserved_name_still_raise():
    """The whole compile must fail clearly rather than silently dropping
    just the colliding function -- a silently-dropped endpoint could be
    just as confusing as a silent auth bypass, so this must be a loud,
    actionable error, not a silent skip.
    """

    functions = [
        {"name": "train_model", "args": [], "return_type": "dict"},
        {"name": "verify_api_key", "args": [], "return_type": "dict"},
    ]

    with pytest.raises(ReservedFunctionNameError):
        generate_fastapi_code(functions)


def test_functions_colliding_on_request_model_name_get_distinct_classes(monkeypatch):
    """Confirmed exploitable before this fix: model_name only uppercased
    the function name's first character, so "get_data" and "Get_data"
    (two distinct, valid Python function names) both produced the class
    name "Get_dataRequest". The second class definition silently shadowed
    the first, so BOTH endpoints resolved to the same class -- the first
    function's endpoint ended up validating requests against the
    *second* function's fields, with no compile-time or runtime error.
    """

    functions = [
        {
            "name": "get_data",
            "args": [{"name": "query", "type": "str", "has_default": False, "kind": "positional"}],
            "return_type": "dict",
        },
        {
            "name": "Get_data",
            "args": [{"name": "id", "type": "int", "has_default": False, "kind": "positional"}],
            "return_type": "dict",
        },
    ]

    code = generate_fastapi_code(functions)

    compile(code, "<generated>", "exec")

    assert code.count("class Get_dataRequest(BaseModel):") == 1
    assert code.count("class Get_dataRequest_2(BaseModel):") == 1
    assert "def get_data(req: Get_dataRequest, " in code
    assert "def Get_data(req: Get_dataRequest_2, " in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    assert "query" in namespace["Get_dataRequest"].model_fields
    assert "id" in namespace["Get_dataRequest_2"].model_fields


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
    assert spec.package_directory() == "run-pipeline-sdk"
    sdk_package = generator.schema_generator.generate_sdk_package(spec.npm_package_name())
    assert '"name": "run-pipeline-sdk"' in sdk_package["package_json"]
    assert '"compilerOptions": {' in sdk_package["tsconfig"]

    sdk_project = generator.schema_generator.generate_sdk_project([spec])
    assert sdk_project.file_count() == 4  # package.json, tsconfig.json, src/index.ts, src/run_pipeline_sdk.ts
    file_names = sdk_project.file_names()
    assert "package.json" in file_names
    assert "tsconfig.json" in file_names
    assert "src/index.ts" in file_names
    assert "src/run_pipeline_sdk.ts" in file_names
    assert "export interface RunPipelineRequest {" in sdk_project.files["src/run_pipeline_sdk.ts"]


def test_performance_report_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, PerformanceReportGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    report = PerformanceReportGenerator().generate()

    assert report.title == "Performance Report"
    assert report.section_count == 7
    assert report.sections == [
        "Performance Assessment",
        "Bottleneck Detection",
        "Scalability Analysis",
        "Capacity Planning",
        "Performance Optimization",
        "Performance Recommendations",
        "Performance Scorecard",
    ]

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.performance_report_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_report = generator.generate_performance_report()
    assert generated_report.title == "Performance Report"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.performance_report_manifest(report)
    assert manifest["title"] == "Performance Report"
    assert manifest["section_count"] == 7


def test_performance_intelligence_control_center_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, PerformanceIntelligenceControlCenterGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    control_center = PerformanceIntelligenceControlCenterGenerator().generate()

    assert control_center.performance_assessment_enabled is True
    assert control_center.bottleneck_detection_enabled is True
    assert control_center.scalability_analysis_enabled is True
    assert control_center.capacity_planning_enabled is True
    assert control_center.performance_optimization_enabled is True
    assert control_center.performance_recommendations_enabled is True
    assert control_center.performance_scorecard_enabled is True
    assert control_center.performance_report_enabled is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.performance_intelligence_control_center_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_control_center = generator.generate_performance_intelligence_control_center()
    assert generated_control_center.performance_report_enabled is True

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.performance_intelligence_manifest(control_center)
    assert manifest["performance_assessment_enabled"] is True
    assert manifest["performance_report_enabled"] is True


def test_performance_automation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, PerformanceAutomationEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    automation = PerformanceAutomationEngine().generate()

    assert automation.workflow_name == "performance_monitoring"
    assert automation.triggers == [
        "latency_threshold_exceeded",
        "throughput_drop_detected",
        "bottleneck_identified",
    ]
    assert automation.actions == [
        "generate_performance_report",
        "notify_platform_team",
        "create_optimization_ticket",
    ]

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.performance_automation_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_automation = generator.generate_performance_automation()
    assert generated_automation.workflow_name == "performance_monitoring"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.performance_automation_manifest(automation)
    assert manifest["workflow_name"] == "performance_monitoring"
    assert manifest["trigger_count"] == 3
    assert manifest["action_count"] == 3


def test_performance_remediation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, PerformanceRemediationEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    remediation = PerformanceRemediationEngine().generate()

    assert remediation.issue_type == "high_latency"
    assert remediation.priority == "high"
    assert remediation.remediation_actions == [
        "optimize_database_queries",
        "increase_cache_hit_rate",
        "scale_application_instances",
        "enable_connection_pooling",
    ]

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.performance_remediation_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_remediation = generator.generate_performance_remediation()
    assert generated_remediation.issue_type == "high_latency"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.performance_remediation_manifest(remediation)
    assert manifest["issue_type"] == "high_latency"
    assert manifest["action_count"] == 4
    assert manifest["priority"] == "high"


def test_performance_governance_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, PerformanceGovernanceEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    governance = PerformanceGovernanceEngine().generate()

    assert governance.performance_owner == "platform_team"
    assert governance.review_frequency == "monthly"
    assert governance.sla_review_required is True
    assert governance.benchmark_review_required is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.performance_governance_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_governance = generator.generate_performance_governance()
    assert generated_governance.performance_owner == "platform_team"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.performance_governance_manifest(governance)
    assert manifest["performance_owner"] == "platform_team"
    assert manifest["review_frequency"] == "monthly"
    assert manifest["sla_review_required"] is True
    assert manifest["benchmark_review_required"] is True


def test_autonomous_performance_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AutonomousPerformanceEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    performance = AutonomousPerformanceEngine().generate()

    assert performance.self_tuning_enabled is True
    assert performance.adaptive_scaling_enabled is True
    assert performance.performance_learning_enabled is True
    assert performance.continuous_optimization_enabled is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.autonomous_performance_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_performance = generator.generate_autonomous_performance()
    assert generated_performance.self_tuning_enabled is True

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.autonomous_performance_manifest(performance)
    assert manifest["self_tuning_enabled"] is True
    assert manifest["adaptive_scaling_enabled"] is True
    assert manifest["performance_learning_enabled"] is True
    assert manifest["continuous_optimization_enabled"] is True


def test_ai_readiness_assessment_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIReadinessAssessmentEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    assessment = AIReadinessAssessmentEngine().generate()

    assert assessment.ai_readiness_score == 94.0
    assert assessment.llm_compatibility_score == 92.0
    assert assessment.agent_readiness_score == 90.0
    assert assessment.ai_readiness_grade == "A"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_readiness_assessment_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_assessment = generator.generate_ai_readiness_assessment()
    assert generated_assessment.ai_readiness_score == 94.0

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_readiness_assessment_manifest(assessment)
    assert manifest["ai_readiness_score"] == 94.0
    assert manifest["llm_compatibility_score"] == 92.0
    assert manifest["agent_readiness_score"] == 90.0
    assert manifest["ai_readiness_grade"] == "A"


def test_llm_integration_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, LLMIntegrationEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    integration = LLMIntegrationEngine().generate()

    assert integration.provider == "OpenAI"
    assert integration.interaction_pattern == "tool_calling"
    assert integration.recommended_model == "gpt-5.5"
    assert integration.prompt_strategy == "structured_system_prompt"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.llm_integration_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_integration = generator.generate_llm_integration()
    assert generated_integration.provider == "OpenAI"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.llm_integration_manifest(integration)
    assert manifest["provider"] == "OpenAI"
    assert manifest["interaction_pattern"] == "tool_calling"
    assert manifest["recommended_model"] == "gpt-5.5"
    assert manifest["prompt_strategy"] == "structured_system_prompt"


def test_rag_intelligence_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, RAGIntelligenceEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    rag = RAGIntelligenceEngine().generate()

    assert rag.retrieval_strategy == "hybrid_search"
    assert rag.embedding_model == "text-embedding-3-large"
    assert rag.vector_database == "Qdrant"
    assert rag.chunking_strategy == "semantic_chunking"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.rag_intelligence_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_rag = generator.generate_rag_intelligence()
    assert generated_rag.retrieval_strategy == "hybrid_search"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.rag_intelligence_manifest(rag)
    assert manifest["retrieval_strategy"] == "hybrid_search"
    assert manifest["embedding_model"] == "text-embedding-3-large"
    assert manifest["vector_database"] == "Qdrant"
    assert manifest["chunking_strategy"] == "semantic_chunking"


def test_ai_agent_architecture_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIAgentArchitectureEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    architecture = AIAgentArchitectureEngine().generate()

    assert architecture.architecture_type == "multi_agent"
    assert architecture.orchestration_strategy == "planner_executor"
    assert architecture.tool_invocation_pattern == "function_calling"
    assert architecture.memory_strategy == "hybrid_memory"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_agent_architecture_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_architecture = generator.generate_ai_agent_architecture()
    assert generated_architecture.architecture_type == "multi_agent"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_agent_architecture_manifest(architecture)
    assert manifest["architecture_type"] == "multi_agent"
    assert manifest["orchestration_strategy"] == "planner_executor"
    assert manifest["tool_invocation_pattern"] == "function_calling"
    assert manifest["memory_strategy"] == "hybrid_memory"


def test_ai_workflow_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIWorkflowEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    workflow = AIWorkflowEngine().generate()

    assert workflow.workflow_name == "agentic_request_processing"
    assert workflow.stages == [
        "request_analysis",
        "retrieval",
        "reasoning",
        "tool_execution",
        "response_generation",
    ]
    assert workflow.execution_strategy == "planner_executor"
    assert workflow.parallel_execution is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_workflow_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_workflow = generator.generate_ai_workflow()
    assert generated_workflow.workflow_name == "agentic_request_processing"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_workflow_manifest(workflow)
    assert manifest["workflow_name"] == "agentic_request_processing"
    assert manifest["stage_count"] == 5
    assert manifest["execution_strategy"] == "planner_executor"
    assert manifest["parallel_execution"] is True


def test_ai_recommendation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIRecommendationEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    recommendations = AIRecommendationEngine().generate()

    assert len(recommendations) == 3
    assert recommendations[0].recommendation == "introduce_long_term_memory"
    assert recommendations[0].category == "agent_memory"
    assert recommendations[0].priority == "high"
    assert recommendations[1].recommendation == "enable_semantic_routing"
    assert recommendations[2].recommendation == "implement_multi_agent_coordination"
    assert recommendations[2].priority == "medium"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_recommendations_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_recommendations = generator.generate_ai_recommendations()
    assert len(generated_recommendations) == 3

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_recommendation_manifest(recommendations)
    assert manifest["recommendation_count"] == 3


def test_ai_scorecard_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIScorecardEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    scorecard = AIScorecardEngine().generate()

    assert scorecard.overall_score == 93.0
    assert scorecard.ai_grade == "A"
    assert scorecard.ai_readiness_score == 94.0
    assert scorecard.llm_compatibility_score == 92.0
    assert scorecard.agent_readiness_score == 90.0
    assert scorecard.recommendation_count == 3

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_scorecard_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_scorecard = generator.generate_ai_scorecard()
    assert generated_scorecard.overall_score == 93.0

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_scorecard_manifest(scorecard)
    assert manifest["overall_score"] == 93.0
    assert manifest["ai_grade"] == "A"
    assert manifest["ai_readiness_score"] == 94.0
    assert manifest["llm_compatibility_score"] == 92.0
    assert manifest["agent_readiness_score"] == 90.0


def test_ai_report_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIReportGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    report = AIReportGenerator().generate()

    assert report.title == "AI Report"
    assert report.section_count == 7
    assert len(report.sections) == 7
    assert report.sections[0] == "AI Readiness Assessment"
    assert report.sections[-1] == "AI Scorecard"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_report_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_report = generator.generate_ai_report()
    assert generated_report.title == "AI Report"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_report_manifest(report)
    assert manifest["title"] == "AI Report"
    assert manifest["section_count"] == 7


def test_ai_intelligence_control_center_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        AIIntelligenceControlCenterGenerator,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    control_center = AIIntelligenceControlCenterGenerator().generate()

    assert control_center.ai_readiness_enabled is True
    assert control_center.llm_integration_enabled is True
    assert control_center.rag_intelligence_enabled is True
    assert control_center.ai_agent_architecture_enabled is True
    assert control_center.ai_workflow_enabled is True
    assert control_center.ai_recommendations_enabled is True
    assert control_center.ai_scorecard_enabled is True
    assert control_center.ai_report_enabled is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_intelligence_control_center_enabled() is True

    generator = PipelineSchemaGenerator()
    generated = generator.generate_ai_intelligence_control_center()
    assert generated.ai_readiness_enabled is True

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_intelligence_manifest(control_center)
    assert manifest["ai_readiness_enabled"] is True
    assert manifest["llm_integration_enabled"] is True
    assert manifest["rag_intelligence_enabled"] is True
    assert manifest["ai_agent_architecture_enabled"] is True
    assert manifest["ai_workflow_enabled"] is True
    assert manifest["ai_recommendations_enabled"] is True
    assert manifest["ai_scorecard_enabled"] is True
    assert manifest["ai_report_enabled"] is True


def test_ai_automation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIAutomationEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    automation = AIAutomationEngine().generate()

    assert automation.workflow_name == "agentic_ai_pipeline"
    assert automation.triggers == [
        "new_user_request",
        "knowledge_base_updated",
        "scheduled_reasoning_cycle",
    ]
    assert automation.actions == [
        "retrieve_context",
        "invoke_llm",
        "execute_tools",
        "generate_response",
    ]

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_automation_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_automation = generator.generate_ai_automation()
    assert generated_automation.workflow_name == "agentic_ai_pipeline"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_automation_manifest(automation)
    assert manifest["workflow_name"] == "agentic_ai_pipeline"
    assert manifest["trigger_count"] == 3
    assert manifest["action_count"] == 4


def test_ai_remediation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIRemediationEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    remediation = AIRemediationEngine().generate()

    assert remediation.issue_type == "llm_failure"
    assert remediation.remediation_actions == [
        "switch_to_backup_model",
        "retry_with_reduced_context",
        "fallback_to_cached_response",
        "notify_ai_operations",
    ]
    assert remediation.priority == "high"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_remediation_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_remediation = generator.generate_ai_remediation()
    assert generated_remediation.issue_type == "llm_failure"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_remediation_manifest(remediation)
    assert manifest["issue_type"] == "llm_failure"
    assert manifest["action_count"] == 4
    assert manifest["priority"] == "high"


def test_ai_governance_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AIGovernanceEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    governance = AIGovernanceEngine().generate()

    assert governance.ai_owner == "ai_platform_team"
    assert governance.model_review_frequency == "monthly"
    assert governance.responsible_ai_review_required is True
    assert governance.model_versioning_required is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.ai_governance_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_governance = generator.generate_ai_governance()
    assert generated_governance.ai_owner == "ai_platform_team"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.ai_governance_manifest(governance)
    assert manifest["ai_owner"] == "ai_platform_team"
    assert manifest["model_review_frequency"] == "monthly"
    assert manifest["responsible_ai_review_required"] is True
    assert manifest["model_versioning_required"] is True


def test_autonomous_ai_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator, AutonomousAIEngine
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    ai = AutonomousAIEngine().generate()

    assert ai.self_learning_enabled is True
    assert ai.adaptive_orchestration_enabled is True
    assert ai.autonomous_reasoning_enabled is True
    assert ai.continuous_improvement_enabled is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.autonomous_ai_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_ai = generator.generate_autonomous_ai()
    assert generated_ai.self_learning_enabled is True

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.autonomous_ai_manifest(ai)
    assert manifest["self_learning_enabled"] is True
    assert manifest["adaptive_orchestration_enabled"] is True
    assert manifest["autonomous_reasoning_enabled"] is True
    assert manifest["continuous_improvement_enabled"] is True


def test_enterprise_readiness_assessment_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        EnterpriseReadinessAssessmentEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    assessment = EnterpriseReadinessAssessmentEngine().generate()

    assert assessment.enterprise_readiness_score == 95.0
    assert assessment.business_readiness_score == 93.0
    assert assessment.organizational_maturity_score == 91.0
    assert assessment.enterprise_grade == "A"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.enterprise_readiness_assessment_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_assessment = generator.generate_enterprise_readiness_assessment()
    assert generated_assessment.enterprise_readiness_score == 95.0

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.enterprise_readiness_assessment_manifest(assessment)
    assert manifest["enterprise_readiness_score"] == 95.0
    assert manifest["business_readiness_score"] == 93.0
    assert manifest["organizational_maturity_score"] == 91.0
    assert manifest["enterprise_grade"] == "A"


def test_platform_readiness_assessment_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformReadinessAssessmentEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    assessment = PlatformReadinessAssessmentEngine().generate()

    assert assessment.platform_readiness_score == 95.0
    assert assessment.developer_experience_score == 93.0
    assert assessment.platform_maturity_score == 92.0
    assert assessment.platform_grade == "A"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_readiness_assessment_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_assessment = generator.generate_platform_readiness_assessment()
    assert generated_assessment.platform_readiness_score == 95.0

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_readiness_assessment_manifest(assessment)
    assert manifest["platform_readiness_score"] == 95.0
    assert manifest["developer_experience_score"] == 93.0
    assert manifest["platform_maturity_score"] == 92.0
    assert manifest["platform_grade"] == "A"


def test_developer_experience_intelligence_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        DeveloperExperienceIntelligenceEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    developer_experience = DeveloperExperienceIntelligenceEngine().generate()

    assert developer_experience.onboarding_experience == "excellent"
    assert developer_experience.self_service_score == 94.0
    assert developer_experience.documentation_quality == "high"
    assert developer_experience.golden_path_available is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.developer_experience_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_developer_experience = generator.generate_developer_experience()
    assert generated_developer_experience.self_service_score == 94.0

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.developer_experience_intelligence_manifest(
        developer_experience
    )
    assert manifest["onboarding_experience"] == "excellent"
    assert manifest["self_service_score"] == 94.0
    assert manifest["documentation_quality"] == "high"
    assert manifest["golden_path_available"] is True


def test_internal_developer_platform_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        InternalDeveloperPlatformEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    platform = InternalDeveloperPlatformEngine().generate()

    assert platform.platform_type == "internal_developer_platform"
    assert platform.developer_portal == "Backstage"
    assert platform.self_service_model == "golden_paths"
    assert platform.software_catalog_enabled is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.internal_developer_platform_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_platform = generator.generate_internal_developer_platform()
    assert generated_platform.platform_type == "internal_developer_platform"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.internal_developer_platform_manifest(platform)
    assert manifest["platform_type"] == "internal_developer_platform"
    assert manifest["developer_portal"] == "Backstage"
    assert manifest["self_service_model"] == "golden_paths"
    assert manifest["software_catalog_enabled"] is True


def test_platform_operations_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformOperationsIntelligenceEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    operations = PlatformOperationsIntelligenceEngine().generate()

    assert operations.operating_model == "platform_as_a_product"
    assert operations.service_ownership == "platform_team"
    assert operations.operational_health == "healthy"
    assert operations.incident_management == "sre_driven"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_operations_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_operations = generator.generate_platform_operations()
    assert generated_operations.operating_model == "platform_as_a_product"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_operations_manifest(operations)
    assert manifest["operating_model"] == "platform_as_a_product"
    assert manifest["service_ownership"] == "platform_team"
    assert manifest["operational_health"] == "healthy"
    assert manifest["incident_management"] == "sre_driven"


def test_platform_recommendation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformRecommendationEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    recommendations = PlatformRecommendationEngine().generate()

    assert len(recommendations) == 3
    assert recommendations[0].recommendation == "expand_golden_path_templates"
    assert recommendations[0].category == "developer_experience"
    assert recommendations[0].priority == "high"
    assert recommendations[1].recommendation == "enable_self_service_provisioning"
    assert recommendations[1].category == "platform_operations"
    assert recommendations[1].priority == "high"
    assert recommendations[2].recommendation == "introduce_platform_scorecards"
    assert recommendations[2].category == "governance"
    assert recommendations[2].priority == "medium"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_recommendations_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_recommendations = generator.generate_platform_recommendations()
    assert generated_recommendations[0].recommendation == "expand_golden_path_templates"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_recommendation_manifest(recommendations)
    assert manifest["recommendation_count"] == 3


def test_platform_report_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformReportGenerator,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    report = PlatformReportGenerator().generate()

    assert report.title == "Platform Report"
    assert report.sections == [
        "Platform Readiness Assessment",
        "Developer Experience",
        "Internal Developer Platform",
        "Platform Engineering Architecture",
        "Platform Operations",
        "Platform Recommendations",
        "Platform Scorecard"
    ]
    assert report.section_count == 7

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_report_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_report = generator.generate_platform_report()
    assert generated_report.title == "Platform Report"
    assert generated_report.section_count == 7

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_report_manifest(report)
    assert manifest["title"] == "Platform Report"
    assert manifest["section_count"] == 7


def test_platform_automation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformAutomationEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    automation = PlatformAutomationEngine().generate()

    assert automation.workflow_name == "platform_self_service"
    assert automation.triggers == [
        "developer_request",
        "repository_created",
        "service_registered"
    ]
    assert automation.actions == [
        "provision_infrastructure",
        "configure_ci_cd",
        "register_service",
        "notify_platform_team"
    ]

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_automation_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_automation = generator.generate_platform_automation()
    assert generated_automation.workflow_name == "platform_self_service"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_automation_manifest(automation)
    assert manifest["workflow_name"] == "platform_self_service"
    assert manifest["trigger_count"] == 3
    assert manifest["action_count"] == 4


def test_platform_remediation_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformRemediationEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    remediation = PlatformRemediationEngine().generate()

    assert remediation.issue_type == "developer_portal_unavailable"
    assert remediation.remediation_actions == [
        "restart_platform_services",
        "rebuild_service_catalog",
        "revalidate_platform_integrations",
        "notify_platform_operations"
    ]
    assert remediation.priority == "high"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_remediation_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_remediation = generator.generate_platform_remediation()
    assert generated_remediation.issue_type == "developer_portal_unavailable"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_remediation_manifest(remediation)
    assert manifest["issue_type"] == "developer_portal_unavailable"
    assert manifest["action_count"] == 4
    assert manifest["priority"] == "high"


def test_platform_governance_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformGovernanceEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    governance = PlatformGovernanceEngine().generate()

    assert governance.platform_owner == "platform_engineering_team"
    assert governance.governance_review_frequency == "monthly"
    assert governance.platform_standards_required is True
    assert governance.developer_experience_review_required is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_governance_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_governance = generator.generate_platform_governance()
    assert generated_governance.platform_owner == "platform_engineering_team"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_governance_manifest(governance)
    assert manifest["platform_owner"] == "platform_engineering_team"
    assert manifest["governance_review_frequency"] == "monthly"
    assert manifest["platform_standards_required"] is True
    assert manifest["developer_experience_review_required"] is True


def test_autonomous_platform_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        AutonomousPlatformEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    platform = AutonomousPlatformEngine().generate()

    assert platform.adaptive_platform_enabled is True
    assert platform.self_service_optimization_enabled is True
    assert platform.developer_experience_learning_enabled is True
    assert platform.continuous_platform_improvement_enabled is True

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.autonomous_platform_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_platform = generator.generate_autonomous_platform()
    assert generated_platform.adaptive_platform_enabled is True

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.autonomous_platform_manifest(platform)
    assert manifest["adaptive_platform_enabled"] is True
    assert manifest["self_service_optimization_enabled"] is True
    assert manifest["developer_experience_learning_enabled"] is True
    assert manifest["continuous_platform_improvement_enabled"] is True


def test_platform_engineering_architecture_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import (
        PipelineSchemaGenerator,
        PlatformEngineeringArchitectureEngine,
    )
    from backend.generator.sdk_release_generator import SDKReleaseGenerator

    architecture = PlatformEngineeringArchitectureEngine().generate()

    assert architecture.architecture_style == "platform_as_a_product"
    assert architecture.platform_services == [
        "developer_portal",
        "software_catalog",
        "ci_cd_platform",
        "observability_platform",
        "secrets_management"
    ]
    assert architecture.service_catalog_enabled is True
    assert architecture.platform_api_model == "self_service"

    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.platform_engineering_architecture_enabled() is True

    generator = PipelineSchemaGenerator()
    generated_architecture = generator.generate_platform_engineering_architecture()
    assert generated_architecture.architecture_style == "platform_as_a_product"

    release_generator = SDKReleaseGenerator()
    manifest = release_generator.platform_engineering_architecture_manifest(architecture)
    assert manifest["architecture_style"] == "platform_as_a_product"
    assert manifest["platform_service_count"] == 5
    assert manifest["service_catalog_enabled"] is True
    assert manifest["platform_api_model"] == "self_service"


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


def test_python_sdk_generation():
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec
    from backend.generator import PipelineSchemaGenerator

    spec = PipelineEndpointSpec(
        endpoint_name="train_model",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )

    generator = PipelineSchemaGenerator()
    python_code = generator.generate_python_sdk(spec)

    assert "class TrainModelClient:" in python_code
    assert "def train_model(" in python_code
    assert "requests.post(" in python_code

    models = generator.generate_python_models(spec)
    assert "class TrainModelRequest(" in models["request"]
    assert "source: str" in models["request"]
    assert "class TrainModelResponse(" in models["response"]
    assert "result: str" in models["response"]

    assert spec.python_package_name() == "train_model_sdk"
    assert spec.python_async_client_name() == "TrainModelAsyncClient"

    assert spec.supports_authentication() is True

    package = generator.generate_python_package(spec)
    assert package.file_count() == 8
    assert package.file_names() == [
        "README.md",
        "__init__.py",
        "async_client.py",
        "client.py",
        "exceptions.py",
        "models.py",
        "pyproject.toml",
        "requirements.txt",
    ]
    assert package.contains_file("client.py") is True
    assert package.contains_file("async_client.py") is True
    assert package.contains_file("nonexistent.py") is False
    assert package.has_client() is True
    assert "from .client import *" in package.files["__init__.py"]
    assert "from .async_client import *" in package.files["__init__.py"]
    assert "from .exceptions import *" in package.files["__init__.py"]
    assert "class TrainModelClient:" in package.files["client.py"]
    assert "class TrainModelAsyncClient:" in package.files["async_client.py"]
    assert "api_key: str | None = None" in package.files["client.py"]
    assert "bearer_token: str | None = None" in package.files["client.py"]
    assert "def build_headers(" in package.files["client.py"]
    assert "api_key: str | None = None" in package.files["async_client.py"]
    assert "bearer_token: str | None = None" in package.files["async_client.py"]
    assert "def build_headers(" in package.files["async_client.py"]
    assert "from .exceptions import (\n    APIError\n)" in package.files["client.py"]
    assert "raise APIError(" in package.files["client.py"]
    assert "max_retries: int = 3" in package.files["client.py"]
    assert "timeout: int = 30" in package.files["client.py"]
    assert "for _ in range(" in package.files["client.py"]
    assert "class TrainModelRequest(" in package.files["models.py"]
    assert "class SDKError(" in package.files["exceptions.py"]
    assert "class RetryError(" in package.files["exceptions.py"]

    # Pagination: method signatures
    assert "page: int = 1" in package.files["client.py"]
    assert "limit: int = 100" in package.files["client.py"]
    assert "page: int = 1" in package.files["async_client.py"]
    assert "limit: int = 100" in package.files["async_client.py"]

    # Pagination: params dict in requests
    assert '"page"' in package.files["client.py"]
    assert '"limit"' in package.files["client.py"]
    assert '"page"' in package.files["async_client.py"]
    assert '"limit"' in package.files["async_client.py"]

    # Pagination: PaginationInfo model included in models.py
    assert "class PaginationInfo(" in package.files["models.py"]
    assert "page: int" in package.files["models.py"]
    assert "total: int" in package.files["models.py"]

    # generate_pagination_models standalone check
    pagination = generator.generate_pagination_models()
    assert "class PaginationInfo(" in pagination
    assert "page: int" in pagination
    assert "limit: int" in pagination
    assert "total: int" in pagination

    # README docs
    assert package.contains_file("README.md") is True
    assert "# train_model_sdk" in package.files["README.md"]
    assert "pip install train_model_sdk" in package.files["README.md"]
    assert "TrainModelClient" in package.files["README.md"]
    assert "POST /train_model" in package.files["README.md"]

    # generate_python_docs standalone check
    readme = generator.generate_python_docs(spec)
    assert "# train_model_sdk" in readme
    assert "pip install train_model_sdk" in readme
    assert "TrainModelClient" in readme

    # PyPI packaging
    assert package.contains_file("pyproject.toml") is True
    assert package.contains_file("requirements.txt") is True
    assert 'name =\n    "train_model_sdk"' in package.files["pyproject.toml"]
    assert "setuptools" in package.files["pyproject.toml"]
    assert "requests>=2.0.0" in package.files["requirements.txt"]
    assert "pydantic>=2.0.0" in package.files["requirements.txt"]
    assert "httpx>=0.25.0" in package.files["requirements.txt"]

    # generate_python_packaging standalone check
    packaging = generator.generate_python_packaging(spec)
    assert "pyproject" in packaging
    assert "requirements" in packaging
    assert "train_model_sdk" in packaging["pyproject"]
    assert "httpx" in packaging["requirements"]

    # PythonPackage.manifest()
    m = package.manifest()
    assert m["file_count"] == 8
    assert "client.py" in m["files"]
    assert "README.md" in m["files"]
    assert "pyproject.toml" in m["files"]

    # generate_release_metadata standalone check
    from backend.generator import SDKReleaseMetadata
    meta = generator.generate_release_metadata(spec, 8)
    assert isinstance(meta, SDKReleaseMetadata)
    assert meta.package_name == "train_model_sdk"
    assert meta.version == "1.0.0"
    assert meta.artifact_count == 8
    assert meta.generated_at != ""

    # generate_release_bundle end-to-end check
    bundle = generator.generate_release_bundle(spec)
    assert "package" in bundle
    assert "metadata" in bundle
    assert "manifest" in bundle
    assert bundle["metadata"].package_name == "train_model_sdk"
    assert bundle["metadata"].artifact_count == 8
    assert bundle["manifest"]["artifact_count"] == 8
    assert "client.py" in bundle["manifest"]["artifacts"]
    assert bundle["package"].has_client() is True

    # supported_sdk_targets on spec
    assert spec.supported_sdk_targets() == ["python", "typescript"]

    # generate_multilanguage_bundle end-to-end check
    from backend.generator import MultiLanguageRelease
    ml_bundle = generator.generate_multilanguage_bundle(spec)
    assert isinstance(ml_bundle, MultiLanguageRelease)

    # manifest structure
    assert "languages" in ml_bundle.manifest
    assert "python" in ml_bundle.manifest["languages"]
    assert "typescript" in ml_bundle.manifest["languages"]
    assert "artifacts" in ml_bundle.manifest
    assert "python" in ml_bundle.manifest["artifacts"]
    assert "typescript" in ml_bundle.manifest["artifacts"]

    # python artifacts nested correctly
    py_artifacts = ml_bundle.manifest["artifacts"]["python"]
    assert py_artifacts["artifact_count"] == 8
    assert "client.py" in py_artifacts["artifacts"]

    # typescript manifest nested correctly
    ts_manifest = ml_bundle.manifest["artifacts"]["typescript"]
    assert "module" in ts_manifest
    assert "package" in ts_manifest
    assert ts_manifest["package"] == "train-model-sdk"

    # metadata
    assert ml_bundle.metadata["release_version"] == "1.0.0"
    assert ml_bundle.metadata["sdk_count"] == 2

    # python and typescript bundles accessible on the release object
    assert ml_bundle.python_bundle["package"].has_client() is True
    assert "sdk" in ml_bundle.typescript_bundle


def test_governance_assessment_engine():
    from backend.generator import GovernanceAssessment, GovernanceAssessmentEngine
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec

    # 1. Verify GovernanceAssessmentEngine
    engine = GovernanceAssessmentEngine()
    assessment = engine.generate()
    assert isinstance(assessment, GovernanceAssessment)
    assert assessment.governance_score == 91.0
    assert assessment.compliance_score == 89.0
    assert assessment.audit_readiness_score == 93.0
    assert assessment.governance_grade == "A"

    # 2. Verify PipelineSchemaGenerator
    schema_gen = PipelineSchemaGenerator()
    assert isinstance(schema_gen.governance_assessment_engine, GovernanceAssessmentEngine)
    gen_assessment = schema_gen.generate_governance_assessment()
    assert gen_assessment.governance_score == 91.0

    # 3. Verify SDKReleaseGenerator
    release_gen = SDKReleaseGenerator()
    manifest = release_gen.governance_assessment_manifest(assessment)
    assert manifest["governance_score"] == 91.0
    assert manifest["compliance_score"] == 89.0
    assert manifest["audit_readiness_score"] == 93.0
    assert manifest["governance_grade"] == "A"

    # 4. Verify PipelineEndpointSpec
    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.governance_assessment_enabled() is True


def test_compliance_intelligence_engine():
    from backend.generator import ComplianceFramework, ComplianceIntelligenceEngine
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec

    # 1. Verify ComplianceIntelligenceEngine
    engine = ComplianceIntelligenceEngine()
    frameworks = engine.generate()
    assert len(frameworks) == 3
    assert all(isinstance(f, ComplianceFramework) for f in frameworks)
    assert frameworks[0].framework_name == "SOC2"
    assert frameworks[0].compliance_status == "partial"
    assert frameworks[0].coverage_percent == 82.0

    # 2. Verify PipelineSchemaGenerator
    schema_gen = PipelineSchemaGenerator()
    assert isinstance(schema_gen.compliance_intelligence_engine, ComplianceIntelligenceEngine)
    gen_frameworks = schema_gen.generate_compliance_frameworks()
    assert len(gen_frameworks) == 3

    # 3. Verify SDKReleaseGenerator
    release_gen = SDKReleaseGenerator()
    manifest = release_gen.compliance_framework_manifest(frameworks)
    assert manifest["framework_count"] == 3

    # 4. Verify PipelineEndpointSpec
    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.compliance_intelligence_enabled() is True


def test_policy_enforcement_engine():
    from backend.generator import PolicyControl, PolicyEnforcementEngine
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec

    # 1. Verify PolicyEnforcementEngine
    engine = PolicyEnforcementEngine()
    controls = engine.generate()
    assert len(controls) == 3
    assert all(isinstance(c, PolicyControl) for c in controls)
    assert controls[0].policy_name == "authentication_required"
    assert controls[0].enforcement_status == "enforced"
    assert controls[0].severity == "critical"

    # 2. Verify PipelineSchemaGenerator
    schema_gen = PipelineSchemaGenerator()
    assert isinstance(schema_gen.policy_enforcement_engine, PolicyEnforcementEngine)
    gen_controls = schema_gen.generate_policy_controls()
    assert len(gen_controls) == 3

    # 3. Verify SDKReleaseGenerator
    release_gen = SDKReleaseGenerator()
    manifest = release_gen.policy_control_manifest(controls)
    assert manifest["control_count"] == 3

    # 4. Verify PipelineEndpointSpec
    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.policy_enforcement_enabled() is True


def test_governance_risk_analysis_engine():
    from backend.generator import GovernanceRisk, GovernanceRiskAnalysisEngine
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec

    # 1. Verify GovernanceRiskAnalysisEngine
    engine = GovernanceRiskAnalysisEngine()
    risks = engine.generate()
    assert len(risks) == 3
    assert all(isinstance(r, GovernanceRisk) for r in risks)
    assert risks[0].risk_name == "incomplete_audit_logging"
    assert risks[0].probability == "medium"
    assert risks[0].impact == "high"

    # 2. Verify PipelineSchemaGenerator
    schema_gen = PipelineSchemaGenerator()
    assert isinstance(schema_gen.governance_risk_analysis_engine, GovernanceRiskAnalysisEngine)
    gen_risks = schema_gen.generate_governance_risks()
    assert len(gen_risks) == 3

    # 3. Verify SDKReleaseGenerator
    release_gen = SDKReleaseGenerator()
    manifest = release_gen.governance_risk_manifest(risks)
    assert manifest["risk_count"] == 3

    # 4. Verify PipelineEndpointSpec
    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.governance_risk_analysis_enabled() is True


def test_audit_readiness_engine():
    from backend.generator import AuditReadiness, AuditReadinessEngine
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec

    # 1. Verify AuditReadinessEngine
    engine = AuditReadinessEngine()
    readiness = engine.generate()
    assert isinstance(readiness, AuditReadiness)
    assert readiness.readiness_score == 92.0
    assert readiness.audit_ready is True
    assert readiness.control_coverage_percent == 95.0
    assert readiness.open_findings_count == 2

    # 2. Verify PipelineSchemaGenerator
    schema_gen = PipelineSchemaGenerator()
    assert isinstance(schema_gen.audit_readiness_engine, AuditReadinessEngine)
    gen_readiness = schema_gen.generate_audit_readiness()
    assert gen_readiness.readiness_score == 92.0

    # 3. Verify SDKReleaseGenerator
    release_gen = SDKReleaseGenerator()
    manifest = release_gen.audit_readiness_manifest(readiness)
    assert manifest["readiness_score"] == 92.0
    assert manifest["audit_ready"] is True
    assert manifest["control_coverage_percent"] == 95.0
    assert manifest["open_findings_count"] == 2

    # 4. Verify PipelineEndpointSpec
    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.audit_readiness_enabled() is True


def test_governance_recommendation_engine():
    from backend.generator import GovernanceRecommendation, GovernanceRecommendationEngine
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator
    from backend.generator.sdk_release_generator import SDKReleaseGenerator
    from backend.analyzer.pipeline_endpoint_spec import PipelineEndpointSpec

    # 1. Verify GovernanceRecommendationEngine
    engine = GovernanceRecommendationEngine()
    recommendations = engine.generate()
    assert len(recommendations) == 3
    assert all(isinstance(r, GovernanceRecommendation) for r in recommendations)
    assert recommendations[0].recommendation == "enable_comprehensive_audit_logging"
    assert recommendations[0].priority == "high"
    assert recommendations[0].impact == "high"

    # 2. Verify PipelineSchemaGenerator
    schema_gen = PipelineSchemaGenerator()
    assert isinstance(schema_gen.governance_recommendation_engine, GovernanceRecommendationEngine)
    gen_recs = schema_gen.generate_governance_recommendations()
    assert len(gen_recs) == 3

    # 3. Verify SDKReleaseGenerator
    release_gen = SDKReleaseGenerator()
    manifest = release_gen.governance_recommendation_manifest(recommendations)
    assert manifest["recommendation_count"] == 3

    # 4. Verify PipelineEndpointSpec
    spec = PipelineEndpointSpec(
        endpoint_name="run_pipeline",
        input_fields=["source"],
        output_fields=["result"],
        execution_stages=1,
        parallelism_score=1.0,
    )
    assert spec.governance_recommendations_enabled() is True