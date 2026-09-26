import json
import socket
import sys
import types
import urllib.error

import pytest

from backend.generator.api_generator import (
    GENERATED_APP_ENV_VARS,
    generate_fastapi_code,
    RESERVED_INFRASTRUCTURE_NAMES,
    ReservedFunctionNameError,
)


@pytest.fixture(autouse=True)
def _fake_resolve_example_test_domain(monkeypatch):
    """Every existing background-webhook test below uses "example.test"
    as its own callback_url host -- chosen precisely because RFC 6761
    guarantees it never resolves in real DNS, so these tests make no real
    network request. _is_unsafe_webhook_host (generator/api_generator.py)
    now resolves a callback_url's own hostname before allowing it, the
    same guard POST /api/notebooks/import-url's own
    _reject_unsafe_import_url_host (backend/routes/upload.py) already
    applies -- which would otherwise reject "example.test" for failing to
    resolve at all, the identical treatment it already gives a genuinely
    private address, purely because of *how* this test suite names its
    fake webhook receiver, not anything a real caller's callback_url
    would ever hit.

    Faking just this one hostname's own resolution to a real, public IP
    keeps every such test's "https://example.test/..." callback_url
    unaffected; every other hostname (see
    test_background_endpoint_rejects_a_private_callback_url_host below)
    still resolves for real, so the guard's actual rejection behavior
    stays covered by a real socket.getaddrinfo call, not a blanket bypass.
    """
    real_getaddrinfo = socket.getaddrinfo

    def _fake_getaddrinfo(host, *args, **kwargs):
        if host == "example.test":
            return [(
                socket.AF_INET, socket.SOCK_STREAM, 6, "",
                ("93.184.216.34", 0),
            )]
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


def test_generated_app_env_vars_default_matches_the_actual_generated_code():
    """GENERATED_APP_ENV_VARS (read back by GET /api/env-vars-preview,
    backend/routes/upload.py) must be the single source of truth
    generate_fastapi_code's own os.getenv(...) calls are built from --
    not a second, independently-maintained copy of the same five
    defaults that could silently drift out of sync with what a compiled
    app.py actually falls back to.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]
    code = generate_fastapi_code(functions)

    assert {entry["name"] for entry in GENERATED_APP_ENV_VARS} == {
        "NOTEBOOK_API_KEY",
        "NOTEBOOK_API_ALLOWED_ORIGINS",
        "NOTEBOOK_API_MAX_REQUEST_BYTES",
        "NOTEBOOK_API_TASK_TTL_SECONDS",
        "NOTEBOOK_API_MAX_TASKS",
        "NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS",
        "NOTEBOOK_API_RATE_LIMIT_PER_MINUTE",
        "NOTEBOOK_API_WEBHOOK_TIMEOUT_SECONDS",
        "NOTEBOOK_API_WEBHOOK_SECRET",
        "NOTEBOOK_API_WEBHOOK_MAX_RETRIES",
        "NOTEBOOK_API_WEBHOOK_RETRY_BACKOFF_SECONDS",
        "NOTEBOOK_API_PUBLIC_URL",
        "NOTEBOOK_API_DISABLE_DOCS",
        "NOTEBOOK_API_REJECT_DEPRECATED",
        "NOTEBOOK_API_ENFORCE_SUNSET",
        "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS",
        "NOTEBOOK_API_JSON_LOGS",
    }

    for entry in GENERATED_APP_ENV_VARS:
        assert f'os.getenv("{entry["name"]}", "{entry["default"]}")' in code
        assert entry["description"]


def test_generate_fastapi_code_bakes_in_the_given_source_notebook_sha256():

    functions = [{"name": "add", "args": [], "return_type": "int"}]
    sha = "a" * 64

    code = generate_fastapi_code(functions, source_notebook_sha256=sha)

    assert f"SOURCE_NOTEBOOK_SHA256 = '{sha}'" in code
    assert '"source_notebook_sha256": SOURCE_NOTEBOOK_SHA256' in code


def test_generate_fastapi_code_defaults_source_notebook_sha256_to_none():

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "SOURCE_NOTEBOOK_SHA256 = None" in code


def test_generate_fastapi_code_bakes_in_the_given_notebook_to_api_version():
    """GET / and GET /info both previously reported a hardcoded "1.0.0"
    literal completely unrelated to which actual version of this tool
    compiled the app -- the same "two independent, inevitably-drifting
    hardcoded version literals" bug NOTEBOOK_TO_API_VERSION
    (backend/compiler.py) was already introduced to deduplicate for this
    dashboard's own GET /api/health and GET /, just never threaded
    through to the *generated* app's own identical two literals.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions, notebook_to_api_version="0.4.2")

    assert "NOTEBOOK_TO_API_VERSION = '0.4.2'" in code
    assert "'generator_version': NOTEBOOK_TO_API_VERSION," in code
    assert '"version": NOTEBOOK_TO_API_VERSION,' in code


def test_generate_fastapi_code_bakes_the_given_version_into_the_fastapi_app_itself():
    """A third hardcoded "1.0.0" literal missed the first time this
    parameter was added: the FastAPI(...) app object's own `version=`
    kwarg, which custom_openapi passes straight through as this app's
    own OpenAPI "info.version" -- user-visible in every compiled app's
    own /docs (Swagger UI), and baked directly into whatever POST
    /api/export-openapi writes out (export_openapi_schema serializes
    app.openapi() unchanged), unlike "generator_version"/"version" above
    (informational JSON fields only).
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions, notebook_to_api_version="0.4.2")

    assert "version='0.4.2'" in code
    assert 'version="1.0.0"' not in code


def test_generate_fastapi_code_defaults_notebook_to_api_version_to_one_point_zero_point_zero():
    """A caller not passing "notebook_to_api_version" at all (a direct
    unit test, most commonly -- every real compile always passes
    compiler.py's own NOTEBOOK_TO_API_VERSION) must see this function's
    own previous literal exactly, not a silently different default no
    caller asked for.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "NOTEBOOK_TO_API_VERSION = '1.0.0'" in code
    assert "version='1.0.0'" in code


def test_notebook_to_api_version_is_a_reserved_infrastructure_name():
    """A notebook function (or module-level assignment) literally named
    NOTEBOOK_TO_API_VERSION would silently rebind the real one at
    module-load time -- the exact endpoint-ordering trap this reserved-
    names set already exists to reject outright, the same protection
    GENERATED_AT/PYTHON_VERSION (the other baked-in metadata constants)
    already have.
    """

    assert "NOTEBOOK_TO_API_VERSION" in RESERVED_INFRASTRUCTURE_NAMES


def test_generate_fastapi_code_passes_servers_to_get_openapi():
    """Confirmed dead code before this fix: the FastAPI(...) constructor's
    own servers=[...] kwarg was silently discarded, since custom_openapi
    completely overrides app.openapi and never itself passed servers= to
    get_openapi(...) -- app.openapi()["servers"] was never even a key in
    the resulting schema, no matter what the constructor was given.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "servers=app.servers," in code


def test_generate_fastapi_code_bakes_the_given_public_url_into_the_servers_entry():
    """NOTEBOOK_API_PUBLIC_URL (read into PUBLIC_URL before app =
    FastAPI(...), since the servers= kwarg needs it at construction time)
    drives the same "servers" entry GET /docs' own Swagger UI "Try it
    out" defaults its request URL to -- left at the previous hardcoded
    "http://localhost:8000" outside local development, every "Try it
    out" request failed from a browser that wasn't itself on the same
    machine as the deployment.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert (
        'PUBLIC_URL = os.getenv("NOTEBOOK_API_PUBLIC_URL", "http://localhost:8000")'
        in code
    )
    assert 'servers=[{"url": PUBLIC_URL, "description": "This deployment"}]' in code


def test_public_url_is_a_reserved_infrastructure_name():

    assert "PUBLIC_URL" in RESERVED_INFRASTRUCTURE_NAMES


def test_custom_openapi_restores_a_none_valued_example_field_get_openapi_drops(
    monkeypatch,
):
    """FastAPI's own get_openapi() (called inside custom_openapi, not
    this project's own code) silently drops any None-valued key from a
    model's own "example" while rebuilding the served schema -- confirmed
    via a real compiled app: BaseModel.model_json_schema() on the exact
    same model correctly keeps {"name": None, "age": 5} (generate_
    example_payload's own "Optional[X] = None keeps its real default"
    fix, backend/parser/ast_parser.py, working as intended), but this
    schema's own components/schemas/GreetRequest/example, reached only
    through get_openapi(), silently came back as {"age": 5} -- the
    "name" key gone entirely, defeating that exact fix the moment anyone
    actually reads the served schema (/docs, /openapi.json, or a
    third-party tool generating a client from it) instead of the
    generated source text directly. The same "the framework silently
    drops/reshapes something this project's own code already got right,
    so compensate for it right where the schema is finalized" pattern
    test_generate_fastapi_code_passes_servers_to_get_openapi above
    already established for a different FastAPI gap.
    """
    from typing import Optional

    functions = [{
        "name": "greet",
        "args": [
            {
                "name": "name", "type": "Optional[str]", "default": None,
                "has_default": True, "kind": "positional",
            },
            {
                "name": "age", "type": "int", "default": 5,
                "has_default": True, "kind": "positional",
            },
        ],
        "return_type": "str",
        "example_payload": {"name": None, "age": 5},
    }]

    code = generate_fastapi_code(functions)

    assert "for _model_name, _model_schema in (" in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].greet = lambda name=None, age=5: name or "anon"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    schema = client.get("/openapi.json").json()

    assert schema["components"]["schemas"]["GreetRequest"]["example"] == {
        "name": None, "age": 5,
    }


def test_custom_openapi_restores_a_none_valued_field_default_get_openapi_drops(
    monkeypatch,
):
    """The identical get_openapi() stripping the test above fixes for a
    model's own top-level "example", one level down: an individual
    field's own "default" whose real, declared value is None.

    Confirmed exploitable via the exact same model: GreetRequest.
    model_json_schema() (pure Pydantic) correctly gives "name" a
    "default": null entry -- Optional[str] = None is a real, optional
    field with a real default, the same as any other -- but this
    schema's own components/schemas/GreetRequest/properties/name never
    got a "default" key at all when reached through get_openapi()
    instead, discarding the field's own actual fallback value entirely
    for anyone reading the served schema (a Swagger UI form, a
    third-party client/contract-testing tool generating from
    /openapi.json) rather than the generated source text directly.
    """
    from typing import Optional

    functions = [{
        "name": "greet",
        "args": [
            {
                "name": "name", "type": "Optional[str]", "default": None,
                "has_default": True, "kind": "positional",
            },
            {
                "name": "age", "type": "int", "default": 5,
                "has_default": True, "kind": "positional",
            },
        ],
        "return_type": "str",
    }]

    code = generate_fastapi_code(functions)

    assert "for _field_name, _field_info in (" in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].greet = lambda name=None, age=5: name or "anon"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    schema = client.get("/openapi.json").json()

    properties = schema["components"]["schemas"]["GreetRequest"]["properties"]
    assert properties["name"]["default"] is None
    assert properties["age"]["default"] == 5


def test_custom_openapi_field_default_restoration_does_not_disturb_a_required_field(
    monkeypatch,
):
    """A field with no default at all (required) must never gain a
    fabricated "default": null it never actually had -- the restoration
    loop only ever fills in a *real* default get_openapi() itself
    dropped, never invents one for a field that has none.
    """

    functions = [{
        "name": "greet",
        "args": [
            {
                "name": "name", "type": "str", "has_default": False,
                "kind": "positional",
            },
        ],
        "return_type": "str",
    }]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].greet = lambda name: name

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    schema = client.get("/openapi.json").json()

    properties = schema["components"]["schemas"]["GreetRequest"]["properties"]
    assert "default" not in properties["name"]


def test_custom_openapi_restores_a_none_valued_response_example_get_openapi_drops(
    monkeypatch,
):
    """The identical get_openapi() stripping the test above fixes for a
    request model's own example, for the *other* place every generated
    endpoint's own example ever lives -- a synchronous endpoint's own
    responses={200: {...}} kwarg, passed straight to @app.post(...)
    rather than through a Pydantic model's own json_schema_extra at all.

    Confirmed exploitable: `def maybe_get(x: int): return None` (any
    function with no return annotation, or one that can genuinely return
    None -- an extremely common real-world shape) has its own generated
    source correctly carrying {"result": None} as this response's
    example (generate_example_response, backend/parser/ast_parser.py),
    but the served schema's own paths/'/maybe_get'/post/responses/'200'/
    content/'application/json'/example came back as {} -- the single
    key's own None value stripped until nothing was left at all.
    """

    functions = [{"name": "maybe_get", "args": [], "return_type": None}]

    code = generate_fastapi_code(functions)

    assert "for _route in app.routes:" in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].maybe_get = lambda: None

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    schema = client.get("/openapi.json").json()

    response_200 = schema["paths"]["/maybe_get"]["post"]["responses"]["200"]
    assert response_200["content"]["application/json"]["example"] == {
        "result": None
    }


def test_custom_openapi_response_example_restoration_does_not_disturb_other_status_codes(
    monkeypatch,
):
    """The restoration loop must only ever fill in what get_openapi()
    itself built for a given status code, never invent or duplicate
    entries across the other responses (401/429/500) an ordinary
    endpoint already carries.
    """

    functions = [{
        "name": "add", "args": [], "return_type": "int",
        "example_response": {"result": 0},
    }]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].add = lambda: 0

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    schema = client.get("/openapi.json").json()

    responses = schema["paths"]["/add"]["post"]["responses"]
    assert responses["200"]["content"]["application/json"]["example"] == {
        "result": 0
    }
    assert responses["401"]["content"]["application/json"]["example"] == {
        "detail": "Invalid API key"
    }
    assert (
        "Rate limit exceeded"
        in responses["429"]["content"]["application/json"]["example"]["detail"]
    )


def test_generate_fastapi_code_defaults_to_docs_enabled():
    """docs_url/redoc_url/openapi_url must default to their own normal
    FastAPI paths -- NOTEBOOK_API_DISABLE_DOCS defaults to "false", so an
    existing deployment that never opts in sees no behavior change.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert 'DISABLE_DOCS = os.getenv("NOTEBOOK_API_DISABLE_DOCS", "false")' in code
    assert 'docs_url=None if DISABLE_DOCS else "/docs"' in code
    assert 'redoc_url=None if DISABLE_DOCS else "/redoc"' in code
    assert 'openapi_url=None if DISABLE_DOCS else "/openapi.json"' in code


def test_disable_docs_is_a_reserved_infrastructure_name():

    assert "DISABLE_DOCS" in RESERVED_INFRASTRUCTURE_NAMES


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


def test_rate_limit_dependency_injects_response_to_set_headers():
    """verify_api_key must accept and forward a `response: Response`
    parameter to _enforce_rate_limit -- confirmed exploitable before this
    fix: without it, _enforce_rate_limit had no way to set headers on a
    request that *succeeds*, so X-RateLimit-Limit/-Remaining/-Reset could
    only ever be attached to the 429 it raises, never to the requests
    leading up to it.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "from fastapi import" in code.splitlines()[0]
    assert "Response" in code.splitlines()[0]
    assert (
        "def verify_api_key(response: Response, x_api_key: str = Header(None)):"
        in code
    )
    assert "def _enforce_rate_limit(api_key, response):" in code
    assert "_enforce_rate_limit(x_api_key, response)" in code
    assert "response.headers['X-RateLimit-Limit'] = str(RATE_LIMIT_PER_MINUTE)" in code
    assert "response.headers['X-RateLimit-Remaining'] = str(remaining)" in code
    assert "response.headers['X-RateLimit-Reset'] = str(reset_at)" in code
    assert "'X-RateLimit-Limit': str(RATE_LIMIT_PER_MINUTE)," in code
    assert "'X-RateLimit-Remaining': '0'," in code
    assert "'X-RateLimit-Reset': str(reset_at)," in code


def test_enforce_rate_limit_is_atomic_under_concurrent_threads(monkeypatch):
    """verify_api_key is a plain synchronous `def`, not `async def` --
    FastAPI runs a synchronous dependency in Starlette's own threadpool,
    not on the single asyncio event loop, so two concurrent requests
    carrying the *same* API key can genuinely run _enforce_rate_limit on
    two different worker threads at once. Confirmed exploitable before
    this fix: with no lock around its own get-then-increment-then-store
    sequence, 20 real concurrent threads (with an artificially widened
    race window, simulating realistic thread-scheduling under load)
    against RATE_LIMIT_PER_MINUTE=5 all went through -- a classic lost-
    update race where every thread reads the same starting count before
    any of them writes back their own increment -- and the final stored
    count was 1, not 20. A threading.Lock around the whole read-modify-
    write makes it atomic across threads (an asyncio.Lock would not:
    that only ever protects against other *coroutines* sharing one event
    loop, not concurrent OS threads).
    """
    import threading

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "import threading" in code
    assert "_RATE_LIMIT_LOCK = threading.Lock()" in code
    assert "with _RATE_LIMIT_LOCK:" in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["RATE_LIMIT_PER_MINUTE"] = 5

    class _SlowDict(dict):
        """Widens the real race window artificially -- without this, the
        real get-then-store sequence is short enough that the race,
        while still present, is rare in a quick test run.
        """
        def get(self, key, default=None):
            value = super().get(key, default)
            import time as _time
            _time.sleep(0.01)
            return value

    namespace["_RATE_LIMIT_WINDOWS"] = _SlowDict()

    class _FakeResponse:
        def __init__(self):
            self.headers = {}

    outcomes = []

    def worker():
        try:
            namespace["_enforce_rate_limit"]("testkey", _FakeResponse())
            outcomes.append("allowed")
        except Exception:
            outcomes.append("blocked")

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("allowed") == 5
    assert outcomes.count("blocked") == 15
    assert namespace["_RATE_LIMIT_WINDOWS"]["testkey"][1] == 20


def test_generated_app_configures_cors_middleware_with_a_permissive_default():
    """Before this, the generated app had no CORS configuration at all --
    a browser-based frontend, the single most common way to actually
    consume a deployed generated API, was blocked by CORS with no way to
    fix it short of hand-editing the generated file. Default is
    permissive ("*") because every endpoint is authenticated via the
    X-API-Key header, not a cookie, so allow_credentials stays False and a
    wildcard origin carries no cross-site credential risk.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "from fastapi.middleware.cors import CORSMiddleware" in code
    assert 'os.getenv("NOTEBOOK_API_ALLOWED_ORIGINS", "*")' in code
    assert "allow_credentials=False" in code
    assert "app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS" in code


def test_generated_app_cors_exposes_the_rate_limit_headers_to_cross_origin_js():
    """Confirmed exploitable before this fix: a browser only ever exposes
    a small built-in safelist of response headers to cross-origin JS
    (Cache-Control, Content-Language, Content-Length, Content-Type,
    Expires, Last-Modified, Pragma) -- X-RateLimit-Limit/-Remaining/-Reset
    and Retry-After (see _enforce_rate_limit) are not on it, so
    `fetch(...).headers.get('X-RateLimit-Remaining')` from cross-origin JS
    always returned null, even though the server sent the header every
    time, unless CORSMiddleware's own expose_headers explicitly lists it.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert (
        "expose_headers=['X-RateLimit-Limit', 'X-RateLimit-Remaining', "
        "'X-RateLimit-Reset', 'Retry-After', "
        "'Deprecation', 'X-Deprecation-Reason', 'Sunset', "
        "'X-Notebook-API-Timeout']"
        in code
    )


def test_notebook_function_named_allowed_origins_is_rejected():
    """ALLOWED_ORIGINS is a module-level name the generated app itself
    defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision hazard
    class as API_KEYS or TASKS.
    """

    functions = [{"name": "ALLOWED_ORIGINS", "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match="ALLOWED_ORIGINS"):
        generate_fastapi_code(functions)


def test_generated_app_configures_a_max_request_body_size_middleware():
    """Before this, every endpoint accepted a JSON request body of any
    size -- unlike this tool's own dashboard /api/upload, which has
    always capped uploads at MAX_UPLOAD_BYTES (see routes/upload.py) for
    exactly this reason. A deployed generated app had no equivalent: one
    oversized request could consume unbounded memory building the body
    before Pydantic ever got a chance to validate or reject it.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "class MaxRequestBodySizeMiddleware:" in code
    assert 'os.getenv("NOTEBOOK_API_MAX_REQUEST_BYTES", "10485760")' in code
    assert "app.add_middleware(MaxRequestBodySizeMiddleware)" in code


def test_generated_app_stamps_security_headers_on_every_response():
    """Confirmed exploitable before this fix: the generated app set none
    of the baseline OWASP-recommended hardening headers (X-Content-Type-
    Options, X-Frame-Options, Referrer-Policy) on any response -- grepped
    for across the whole file, zero hits. Registered *after*
    MaxRequestBodySizeMiddleware/CORSMiddleware (see this middleware's
    own comment) so it ends up outermost -- these headers must land on
    every response, not just a successful one.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "@app.middleware('http')" in code
    assert "async def _add_security_headers(request, call_next):" in code
    assert "response.headers['X-Content-Type-Options'] = 'nosniff'" in code
    assert "response.headers['X-Frame-Options'] = 'DENY'" in code
    assert "response.headers['Referrer-Policy'] = 'no-referrer'" in code
    # Registered after (not before) MaxRequestBodySizeMiddleware -- the
    # middleware added last ends up outermost, so this must appear later
    # in the generated source than that registration.
    assert code.index("app.add_middleware(MaxRequestBodySizeMiddleware)") < code.index(
        "async def _add_security_headers"
    )


def test_generated_app_configures_gzip_response_compression():
    """Confirmed exploitable before this fix: the generated app never
    compressed any response -- grepped for across the whole file, zero
    hits -- even though a notebook function's own result, or GET /tasks'
    still-up-to-100-entries-per-page (see the status/limit/offset
    pagination this file's own list_tasks adds), can be large. Registered
    *after* _add_security_headers (see that middleware's own comment on
    why registration order determines outermost-ness) so it compresses
    the truly final response body, not something an inner layer might
    still rewrite.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "from fastapi.middleware.gzip import GZipMiddleware" in code
    assert "app.add_middleware(GZipMiddleware)" in code
    assert code.index("async def _add_security_headers") < code.index(
        "app.add_middleware(GZipMiddleware)"
    )


def test_generated_app_stamps_x_process_time_ms_on_every_response():
    """Confirmed exploitable before this fix: the generated app gave an
    operator no way to see per-request latency short of instrumenting it
    externally -- grepped for across the whole file, no X-Process-Time
    header anywhere. Registered *after* GZipMiddleware (see that
    middleware's own comment on registration order) so the timer spans
    every other layer too, not just handler time.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "async def _add_process_time_header(request, call_next):" in code
    assert "start_time = time.perf_counter()" in code
    assert "response.headers['X-Process-Time-Ms'] = " in code
    assert code.index("app.add_middleware(GZipMiddleware)") < code.index(
        "async def _add_process_time_header"
    )


def test_generated_app_stamps_x_request_id_and_honors_a_caller_supplied_one():
    """Confirmed exploitable before this fix: the generated app never
    surfaced a request-correlation id at all -- grepped for across the
    whole file, no X-Request-ID anywhere -- so a caller had no shared id
    to search server-side logs by when investigating a specific failed
    call after the fact. Must honor a caller-supplied X-Request-ID
    instead of always minting a fresh one, so a trace started upstream
    (e.g. by a gateway already assigning one) isn't forked into two
    disconnected ids.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "async def _add_request_id_header(request, call_next):" in code
    assert (
        "request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())"
        in code
    )
    assert "response.headers['X-Request-ID'] = request_id" in code
    assert code.index("async def _add_process_time_header") < code.index(
        "async def _add_request_id_header"
    )


def test_generated_app_configures_a_json_request_log_middleware_registered_outermost():
    """Confirmed missing before this feature: the only per-request record
    this app ever produced was uvicorn's own default plain-text access
    log line, with no request_id of its own and no duration -- grepped
    for across the whole file, no structured per-request logging
    anywhere. Registered *after* _add_request_id_header (see that
    middleware's own comment on registration order) so it reads back the
    *final* X-Request-ID/X-Process-Time-Ms headers that middleware and
    _add_process_time_header already set, rather than re-deriving either
    one itself.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert (
        'JSON_REQUEST_LOGS = os.getenv("NOTEBOOK_API_JSON_LOGS", "false")'
        '.strip().lower() in ("true", "1", "yes", "on")' in code
    )
    assert "async def _log_request_json(request, call_next):" in code
    assert "if JSON_REQUEST_LOGS:" in code
    assert "print(json.dumps(entry), flush=True)" in code
    assert "'request_id': response.headers.get('X-Request-ID')," in code
    assert "'method': request.method," in code
    assert "'path': request.url.path," in code
    assert "'status_code': response.status_code," in code
    assert (
        "'duration_ms': float(response.headers.get('X-Process-Time-Ms', "
        "'0'))," in code
    )
    assert code.index("async def _add_request_id_header") < code.index(
        "async def _log_request_json"
    )


def test_json_request_logs_are_off_by_default(monkeypatch, capsys):
    """NOTEBOOK_API_JSON_LOGS unset must reproduce this app's previous
    stdout output exactly -- no JSON line printed at all, only whatever
    uvicorn's own access log (not exercised by TestClient) would produce.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].add = lambda a=0, b=0: a + b

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    capsys.readouterr()  # discard any startup output
    resp = client.post(
        "/add", json={"a": 1, "b": 2},
        headers={"X-API-Key": "notebook-to-api-dev-key"},
    )
    assert resp.status_code == 200

    assert capsys.readouterr().out == ""


def test_json_request_logs_emit_a_structured_line_matching_the_response_headers(
    monkeypatch, capsys
):
    """When enabled, the printed JSON line's own "request_id"/
    "status_code" must match the exact X-Request-ID/status this same
    response actually carries -- reused, not re-derived, from
    _add_request_id_header/_add_process_time_header's own headers, so
    a caller correlating its own logs against those headers can never
    see this line disagree with them.
    """

    monkeypatch.setenv("NOTEBOOK_API_JSON_LOGS", "true")

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].add = lambda a=0, b=0: a + b

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    capsys.readouterr()
    resp = client.post(
        "/add", json={"a": 1, "b": 2},
        headers={"X-API-Key": "notebook-to-api-dev-key", "X-Request-ID": "abc-123"},
    )
    assert resp.status_code == 200

    printed_lines = [
        line for line in capsys.readouterr().out.splitlines() if line.strip()
    ]
    assert len(printed_lines) == 1

    log_entry = json.loads(printed_lines[0])
    assert log_entry["request_id"] == "abc-123" == resp.headers["X-Request-ID"]
    assert log_entry["method"] == "POST"
    assert log_entry["path"] == "/add"
    assert log_entry["status_code"] == 200 == resp.status_code
    assert log_entry["duration_ms"] == float(resp.headers["X-Process-Time-Ms"])
    assert isinstance(log_entry["timestamp"], float)
    assert log_entry["deprecated"] is False
    assert "client_ip" not in log_entry and "user_agent" not in log_entry


def test_json_request_logs_accepts_common_truthy_spellings(monkeypatch, capsys):

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    for truthy_value in ("true", "TRUE", "1", "yes", "on"):

        monkeypatch.setenv("NOTEBOOK_API_JSON_LOGS", truthy_value)

        code = generate_fastapi_code(functions)

        _register_fake_notebook_module(monkeypatch)
        namespace = {}
        exec(compile(code, "<generated>", "exec"), namespace)
        namespace["notebook_module"].add = lambda a=0, b=0: a + b

        from fastapi.testclient import TestClient

        client = TestClient(namespace["app"])
        capsys.readouterr()
        resp = client.post(
            "/add", json={"a": 1, "b": 2},
            headers={"X-API-Key": "notebook-to-api-dev-key"},
        )
        assert resp.status_code == 200
        assert capsys.readouterr().out.strip() != "", truthy_value


def test_json_request_logs_and_json_module_are_reserved_infrastructure_names():
    """JSON_REQUEST_LOGS -- see its own RESERVED_INFRASTRUCTURE_NAMES
    entry: a notebook function of this exact name wouldn't crash
    anything, but would silently turn JSON request logging permanently
    on (a function object's truthiness is always True) regardless of
    NOTEBOOK_API_JSON_LOGS. "json" -- _log_request_json's own
    json.dumps({...}) call, reached on every request once enabled, plus
    the pre-existing _deliver_task_webhook's own json.dumps(payload).
    """

    assert "JSON_REQUEST_LOGS" in RESERVED_INFRASTRUCTURE_NAMES
    assert "json" in RESERVED_INFRASTRUCTURE_NAMES


def test_notebook_function_named_json_request_logs_is_rejected():

    functions = [{"name": "JSON_REQUEST_LOGS", "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match="JSON_REQUEST_LOGS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_json_is_rejected():
    """Confirmed exploitable before this fix: `def json(...):` compiled
    fine and silently overwrote the real `json` module reference at
    module-execution time -- the next _log_request_json (with
    NOTEBOOK_API_JSON_LOGS enabled) or _deliver_task_webhook (background
    task webhook delivery) call reached it via json.dumps(...) and
    crashed with "'function' object has no attribute 'dumps'".
    """

    functions = [{"name": "json", "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match="json"):
        generate_fastapi_code(functions)


def test_generated_app_exposes_get_config_reporting_its_own_runtime_limits(monkeypatch):
    """Confirmed exploitable before this fix: every NOTEBOOK_API_* limit
    this app enforces (MAX_REQUEST_BODY_BYTES, TASK_TTL_SECONDS,
    MAX_PENDING_TASKS, RATE_LIMIT_PER_MINUTE, ALLOWED_ORIGINS,
    DISABLE_DOCS, PUBLIC_URL) was only discoverable by reading the
    deployment's own environment directly -- shell access to the
    container -- with /auth/info's own "rate_limit_per_minute" the sole
    exception. The same "ask the running app what it's actually
    configured with" gap GET /api/config already closes for this
    dashboard's own configuration (routes/upload.py), never given an
    equivalent here. No secrets here (API_KEYS' own values are
    deliberately never returned), so this needs no authentication.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "@app.get('/config')" in code
    assert "def service_config():" in code
    for field in (
        "'max_request_body_bytes': MAX_REQUEST_BODY_BYTES,",
        "'task_ttl_seconds': TASK_TTL_SECONDS,",
        "'max_pending_tasks': MAX_PENDING_TASKS,",
        "'webhook_timeout_seconds': WEBHOOK_TIMEOUT_SECONDS,",
        "'webhook_signing_enabled': bool(WEBHOOK_SECRET),",
        "'webhook_max_retries': WEBHOOK_MAX_RETRIES,",
        "'webhook_retry_backoff_seconds': WEBHOOK_RETRY_BACKOFF_SECONDS,",
        "'rate_limit_per_minute': RATE_LIMIT_PER_MINUTE or None,",
        "'allowed_origins': ALLOWED_ORIGINS,",
        "'disable_docs': DISABLE_DOCS,",
        "'public_url': PUBLIC_URL,",
        "'json_logs_enabled': JSON_REQUEST_LOGS,",
    ):
        assert field in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    resp = client.get("/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["max_request_body_bytes"] == 10 * 1024 * 1024
    assert body["task_ttl_seconds"] == 3600
    assert body["max_pending_tasks"] == 10000
    assert body["webhook_timeout_seconds"] == 5
    assert body["webhook_signing_enabled"] is False
    assert body["webhook_max_retries"] == 0
    assert body["webhook_retry_backoff_seconds"] == 0.5
    assert body["rate_limit_per_minute"] is None
    assert body["allowed_origins"] == ["*"]
    assert body["disable_docs"] is False
    assert body["public_url"] == "http://localhost:8000"
    assert body["json_logs_enabled"] is False


def test_generated_app_exposes_get_metrics_as_json(monkeypatch):

    functions = [{"name": "train_model", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: "done"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    client.post("/train_model", json={}, headers=headers)

    resp = client.get("/metrics")

    assert resp.status_code == 200
    body = resp.json()

    # duration_ms_sum is real wall-clock time accumulated across the one
    # request made above -- non-deterministic, so only its type/sign is
    # checked, not an exact value; everything else compares exactly.
    duration_ms_sum = body.pop("http_request_duration_ms_sum")
    assert isinstance(duration_ms_sum, float)
    assert duration_ms_sum >= 0

    assert body == {
        "total_tasks": 1, "processing": 0, "completed": 1, "failed": 0,
        # The one POST /train_model above -- a 200 -- and nothing else:
        # GET /metrics' own request hasn't been counted yet at the point
        # its own handler builds this response body (see
        # _track_http_metrics' own comment -- it increments _HTTP_METRICS
        # only *after* call_next returns, i.e. after this exact dict was
        # already built).
        "http_requests_total": 1,
        "http_requests_by_status_class": {
            "1xx": 0, "2xx": 1, "3xx": 0, "4xx": 0, "5xx": 0,
        },
        # No callback_url was ever given above -- no webhook delivery or
        # redelivery has happened, so every outcome stays at 0.
        "webhook_deliveries_by_outcome": {"delivered": 0, "failed": 0},
        "webhook_redeliveries_by_outcome": {"delivered": 0, "failed": 0},
        "deprecated_endpoint_calls": {},
        "deprecated_endpoint_rejections": {},
        "request_timeouts_by_endpoint": {},
        "rate_limited_by_endpoint": {},
        "task_timeouts_by_endpoint": {},
    }


def test_generated_app_exposes_get_metrics_prometheus(monkeypatch):
    """Confirmed missing before this feature: GET /metrics already
    reported this app's own task counts, but only as a JSON object --
    exactly the wrong shape for the far more common real consumer of a
    "/metrics" path by convention (Prometheus, and anything speaking its
    own text exposition format), which has no way to scrape this app's
    own task counts without a separate translation sidecar in between.
    """

    functions = [{"name": "train_model", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: "done"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    client.post("/train_model", json={}, headers=headers)

    resp = client.get("/metrics/prometheus")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"

    body = resp.text
    assert "# HELP notebook_api_tasks_total" in body
    assert "# TYPE notebook_api_tasks_total gauge" in body
    assert "notebook_api_tasks_total 1" in body
    assert "notebook_api_tasks_processing 0" in body
    assert "notebook_api_tasks_completed 1" in body
    assert "notebook_api_tasks_failed 0" in body
    assert "# TYPE notebook_api_uptime_seconds counter" in body
    assert "notebook_api_uptime_seconds " in body

    # The one POST /train_model above -- a 200, bucketed "2xx" -- and
    # every other status_class still present at 0 (see
    # _track_http_metrics' own comment: always emits all five buckets so
    # a graph plotting one from this app's own startup has no gap at the
    # start).
    assert "# HELP notebook_api_http_requests_total" in body
    assert "# TYPE notebook_api_http_requests_total counter" in body
    assert 'notebook_api_http_requests_total{status_class="1xx"} 0' in body
    assert 'notebook_api_http_requests_total{status_class="2xx"} 1' in body
    assert 'notebook_api_http_requests_total{status_class="3xx"} 0' in body
    assert 'notebook_api_http_requests_total{status_class="4xx"} 0' in body
    assert 'notebook_api_http_requests_total{status_class="5xx"} 0' in body
    assert "# TYPE notebook_api_http_request_duration_ms_sum counter" in body
    assert "notebook_api_http_request_duration_ms_sum " in body


def test_generated_app_metrics_prometheus_counts_a_client_error(monkeypatch):
    """A request the rate limiter/auth layer rejects outright (a missing
    X-API-Key, here) still counts as real request-handling time this app
    actually spent -- _track_http_metrics is registered outermost,
    wrapping verify_api_key's own 401 the same way _log_request_json
    already wraps every other short-circuited response (see that
    middleware's own comment), so a caller hammering this app with bad
    credentials is still visible here as 4xx traffic, not silently
    invisible to this counter.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].add = lambda a, b: a + b

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])

    resp = client.post("/add", json={"a": 1, "b": 2})
    assert resp.status_code == 401

    prom = client.get("/metrics/prometheus")
    assert 'notebook_api_http_requests_total{status_class="4xx"} 1' in prom.text
    assert 'notebook_api_http_requests_total{status_class="2xx"} 0' in prom.text


def test_notebook_function_named_http_metrics_is_rejected():
    """_HTTP_METRICS is a module-level dict both _track_http_metrics (a
    middleware that runs on literally every request) and
    metrics()/metrics_prometheus() read/write by name -- same collision
    hazard class as TASKS.
    """

    functions = [
        {"name": "_HTTP_METRICS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_HTTP_METRICS"):
        generate_fastapi_code(functions)


def test_generated_app_metrics_prometheus_requires_no_api_key(monkeypatch):
    """A Prometheus scrape target is hit unattended on a timer by a
    scraper whose own config supports only a handful of fixed auth
    schemes -- not this app's own X-API-Key header -- so requiring one
    here would make this endpoint unreachable from a real Prometheus
    instance's default configuration, the same reasoning GET
    /metrics/GET /health already have no Depends(verify_api_key) either.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].add = lambda a, b: a + b

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])

    resp = client.get("/metrics/prometheus")

    assert resp.status_code == 200


def test_generated_app_metrics_prometheus_reflects_a_failed_task(monkeypatch):

    functions = [{"name": "train_model", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    def _blows_up():
        raise ValueError("boom")

    namespace["notebook_module"].train_model = _blows_up

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    client.post("/train_model", json={}, headers=headers)

    resp = client.get("/metrics/prometheus")

    assert resp.status_code == 200
    assert "notebook_api_tasks_failed 1" in resp.text
    assert "notebook_api_tasks_completed 0" in resp.text


def test_notebook_function_named_metrics_prometheus_is_rejected():
    """metrics_prometheus is a reserved infrastructure name (GET
    /metrics/prometheus) -- same collision hazard class as
    service_info/metrics/uptime.
    """

    functions = [
        {"name": "metrics_prometheus", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="metrics_prometheus"):
        generate_fastapi_code(functions)


def test_notebook_function_named_task_status_counts_is_rejected():
    """_task_status_counts is a module-level helper both GET /metrics and
    GET /metrics/prometheus call by name -- same collision hazard class
    as _evict_expired_tasks/_run_background_task: a notebook function of
    this exact name would silently overwrite it, breaking both endpoints
    at once.
    """

    functions = [
        {"name": "_task_status_counts", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_task_status_counts"):
        generate_fastapi_code(functions)


def test_notebook_function_named_service_config_is_rejected():
    """service_config is a reserved infrastructure name (GET /config) --
    same collision hazard class as service_info/metrics/uptime.
    """

    functions = [{"name": "service_config", "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match="service_config"):
        generate_fastapi_code(functions)


def test_notebook_function_named_max_request_body_bytes_is_rejected():
    """MAX_REQUEST_BODY_BYTES is a module-level name the generated app
    itself defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision
    hazard class as API_KEYS, TASKS, or ALLOWED_ORIGINS.
    """

    functions = [
        {"name": "MAX_REQUEST_BODY_BYTES", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="MAX_REQUEST_BODY_BYTES"):
        generate_fastapi_code(functions)


def test_notebook_function_named_evict_expired_tasks_is_rejected():
    """_evict_expired_tasks is a module-level helper the generated app
    itself defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision
    hazard class as ALLOWED_ORIGINS or MAX_PENDING_TASKS, but for a
    private function rather than a constant. Confirmed exploitable
    before this was added: a notebook function of this exact name
    compiled fine and silently overwrote the real helper at module-
    execution time (Python has no protection against redefining a name),
    breaking every *other* background endpoint's own submission too,
    since each one calls this same now-shadowed name before enqueuing a
    new task.
    """

    functions = [
        {"name": "_evict_expired_tasks", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_evict_expired_tasks"):
        generate_fastapi_code(functions)


def test_notebook_function_named_run_background_task_is_rejected():
    """_run_background_task is a module-level helper the generated app
    itself defines -- every background endpoint's own submission passes
    this exact name to background_tasks.add_task(...) to actually run
    the task, so a notebook function shadowing it would silently break
    execution of *every* background task in the app, not just the
    notebook's own colliding endpoint.
    """

    functions = [
        {"name": "_run_background_task", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_run_background_task"):
        generate_fastapi_code(functions)


def test_notebook_function_named_task_ttl_seconds_is_rejected():
    """TASK_TTL_SECONDS is read by name from inside _evict_expired_tasks'
    own body -- same collision hazard class as _evict_expired_tasks/
    _run_background_task themselves, just for a constant one of them
    reads rather than the helper itself.
    """

    functions = [
        {"name": "TASK_TTL_SECONDS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="TASK_TTL_SECONDS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_source_notebook_sha256_is_rejected():
    """SOURCE_NOTEBOOK_SHA256 is assigned this compile's own real content
    hash once, at module load, then read back verbatim by GET /info --
    same collision hazard class as every other module-level constant
    here.
    """

    functions = [
        {"name": "SOURCE_NOTEBOOK_SHA256", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="SOURCE_NOTEBOOK_SHA256"):
        generate_fastapi_code(functions)


def test_notebook_function_named_enforce_rate_limit_is_rejected():
    """_enforce_rate_limit is a module-level helper verify_api_key's own
    body calls by name on every single request (via Depends(verify_api_key)
    on literally every endpoint) -- same collision hazard class as
    _evict_expired_tasks/_run_background_task, just for the rate-limiting
    subsystem instead of the background-task one.
    """

    functions = [
        {"name": "_enforce_rate_limit", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_enforce_rate_limit"):
        generate_fastapi_code(functions)


def test_notebook_function_named_rate_limit_per_minute_is_rejected():
    """RATE_LIMIT_PER_MINUTE is read by name from inside
    _enforce_rate_limit's own body -- one level removed from
    _enforce_rate_limit's own name, but the identical exposure: every
    endpoint's own Depends(verify_api_key) calls _enforce_rate_limit,
    which reads this constant.
    """

    functions = [
        {"name": "RATE_LIMIT_PER_MINUTE", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="RATE_LIMIT_PER_MINUTE"):
        generate_fastapi_code(functions)


def test_notebook_function_named_rate_limit_window_seconds_is_rejected():

    functions = [
        {"name": "RATE_LIMIT_WINDOW_SECONDS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="RATE_LIMIT_WINDOW_SECONDS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_rate_limit_windows_is_rejected():

    functions = [
        {"name": "_RATE_LIMIT_WINDOWS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_RATE_LIMIT_WINDOWS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_rate_limit_lock_is_rejected():
    """_RATE_LIMIT_LOCK -- the threading.Lock _enforce_rate_limit uses to
    make its own read-modify-write of _RATE_LIMIT_WINDOWS atomic across
    concurrent worker threads -- is read by name from inside that same
    function, resolved at call time. A notebook function of this exact
    name would rebind it to a function object at module-execution time;
    the very next request under a nonzero RATE_LIMIT_PER_MINUTE would
    then fail with a bare 500 the moment `with _RATE_LIMIT_LOCK:` tried
    to use a function object as a context manager -- the identical
    "one bad name breaks a subsystem every endpoint depends on" exposure
    _RATE_LIMIT_WINDOWS' own entry above already guards against.
    """

    functions = [
        {"name": "_RATE_LIMIT_LOCK", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_RATE_LIMIT_LOCK"):
        generate_fastapi_code(functions)


@pytest.mark.parametrize(
    "name",
    [
        "hmac", "uuid", "time", "jsonable_encoder", "Depends", "BackgroundTasks",
        "HTTPException", "Optional", "get_openapi", "urlparse",
    ],
)
def test_notebook_function_named_after_a_reserved_import_is_rejected(name):
    """Every one of these is a plain top-level `import` this file itself
    makes -- never previously reserved at all, on the untested assumption
    that only names this file *defines* (a constant, a helper) were at
    risk. A notebook function later defined with the same name rebinds
    the import exactly the same way `TASKS = {}` gets rebound by a
    colliding constant name -- and each of these is read back by that
    same bare name, at call time, from somewhere every request (or every
    background one) actually reaches. See RESERVED_INFRASTRUCTURE_NAMES'
    own comment for exactly what each one breaks when shadowed -- from a
    500 on a completely unrelated endpoint (hmac, jsonable_encoder) to
    the entire generated module failing to import at all (Optional).
    """

    functions = [{"name": name, "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match=name):
        generate_fastapi_code(functions)


def test_background_endpoint_rejects_new_tasks_past_max_pending_tasks():
    """_evict_expired_tasks bounds TASKS' long-term growth, but a burst of
    background requests arriving faster than TASK_TTL_SECONDS still grew
    TASKS without limit in the meantime -- nothing stopped a client from
    submitting far more tasks than the process could ever get to,
    exhausting memory well before any of them would expire.
    """

    functions = [{"name": "train_model", "args": [], "return_type": "str"}]

    code = generate_fastapi_code(functions)

    assert 'os.getenv("NOTEBOOK_API_MAX_TASKS", "10000")' in code
    assert "if len(TASKS) >= MAX_PENDING_TASKS:" in code
    assert "status_code=503" in code


def test_background_endpoint_max_pending_tasks_is_atomic_under_concurrent_threads(
    monkeypatch,
):
    """The background-task submission endpoint is a plain synchronous
    `def`, not `async def` -- FastAPI runs it in Starlette's own
    threadpool, not the single asyncio event loop, so two concurrent
    submissions can genuinely run its own admission-control sequence
    (evict, check len(TASKS) against MAX_PENDING_TASKS, insert) on two
    different worker threads at once. Confirmed exploitable before this
    fix: with no lock around that sequence, 20 real concurrent threads
    (with an artificially widened race window, simulating realistic
    thread-scheduling under load) against MAX_PENDING_TASKS=5 all
    succeeded -- every thread read the same not-yet-at-capacity count
    before any of them inserted -- leaving 20 entries in TASKS instead of
    the intended cap of 5, the exact unbounded-memory-growth failure mode
    this check exists to prevent.
    """
    import threading

    functions = [{"name": "train_model", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "_TASKS_ADMISSION_LOCK = threading.Lock()" in code
    assert "with _TASKS_ADMISSION_LOCK:" in code

    parent = types.ModuleType("generated")
    runtime_pkg = types.ModuleType("generated.runtime")
    notebook_module = types.ModuleType("generated.runtime.notebook_module")
    notebook_module.train_model = lambda x: x
    monkeypatch.setitem(sys.modules, "generated", parent)
    monkeypatch.setitem(sys.modules, "generated.runtime", runtime_pkg)
    monkeypatch.setitem(
        sys.modules, "generated.runtime.notebook_module", notebook_module
    )

    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["MAX_PENDING_TASKS"] = 5

    class _SlowLenDict(dict):
        """Widens the real race window artificially -- without this, the
        real check-then-insert sequence is short enough that the race,
        while still present, is rare in a quick test run.
        """
        def __len__(self):
            count = super().__len__()
            import time as _time
            _time.sleep(0.01)
            return count

    namespace["TASKS"] = _SlowLenDict()

    class _FakeReq:
        x = 1

    class _FakeBackgroundTasks:
        def add_task(self, *args, **kwargs):
            pass

    outcomes = []

    def worker():
        try:
            namespace["train_model"](_FakeReq(), _FakeBackgroundTasks())
            outcomes.append("admitted")
        except Exception:
            outcomes.append("rejected")

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("admitted") == 5
    assert outcomes.count("rejected") == 15
    assert len(namespace["TASKS"]) == 5


def test_background_endpoint_retried_with_the_same_idempotency_key_does_not_rerun_it(
    monkeypatch,
):
    """A background endpoint retried after an ambiguous connection
    failure (the exact case both generated SDK clients' own retry logic
    handles -- see sdk_generator.py's _request/requestWithRetry) must not
    run the underlying notebook function a second time just because the
    caller couldn't tell whether its first request actually landed.
    Confirmed exploitable before this fix: nothing here tracked a
    caller-supplied "Idempotency-Key" header at all, so a second POST
    with the same key -- indistinguishable, from this app's own
    perspective, from a genuinely new submission -- always created a
    brand new task_id and enqueued the notebook function again.
    """
    functions = [{"name": "train_model", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    assert "IDEMPOTENCY_KEYS = {}" in code
    assert 'Header(None, alias="Idempotency-Key")' in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    call_count = {"n": 0}

    def fake_train_model():
        call_count["n"] += 1
        return 42

    namespace["notebook_module"].train_model = fake_train_model

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {
        "X-API-Key": "notebook-to-api-dev-key",
        "Idempotency-Key": "retry-key-123",
    }

    first = client.post("/train_model", json={}, headers=headers)
    second = client.post("/train_model", json={}, headers=headers)

    assert first.status_code == 200 == second.status_code
    assert first.json() == {"task_id": second.json()["task_id"], "status": "processing"}
    assert len(namespace["TASKS"]) == 1

    # Give the single background task a moment to actually run before
    # asserting it only ran once -- BackgroundTasks executes after the
    # response is sent, not synchronously inside the POST above.
    import time as _time

    for _ in range(50):
        if namespace["TASKS"][first.json()["task_id"]].get("status") != "processing":
            break
        _time.sleep(0.01)

    assert call_count["n"] == 1


def test_background_endpoint_with_a_fresh_idempotency_key_each_time_creates_two_tasks(
    monkeypatch,
):
    """The opposite of the test above: two submissions that each carry
    their own distinct Idempotency-Key (an ordinary, non-retry caller
    that always generates a fresh one per logical call, the same way
    both generated SDK clients do) must still create two independent
    tasks -- the dedup above must never collapse unrelated calls just
    because both happened to supply *some* key.
    """
    functions = [{"name": "train_model", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: 42

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])

    first = client.post(
        "/train_model", json={},
        headers={"X-API-Key": "notebook-to-api-dev-key", "Idempotency-Key": "key-a"},
    )
    second = client.post(
        "/train_model", json={},
        headers={"X-API-Key": "notebook-to-api-dev-key", "Idempotency-Key": "key-b"},
    )

    assert first.status_code == 200 == second.status_code
    assert first.json()["task_id"] != second.json()["task_id"]
    assert len(namespace["TASKS"]) == 2


def test_background_endpoint_without_an_idempotency_key_still_works_as_before(
    monkeypatch,
):
    """No regression for the overwhelming majority of existing callers,
    including every already-generated SDK client out there predating
    this feature: omitting the header entirely must behave exactly as it
    always has, creating a new task per call with no dedup applied.
    """
    functions = [{"name": "train_model", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: 42

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    first = client.post("/train_model", json={}, headers=headers)
    second = client.post("/train_model", json={}, headers=headers)

    assert first.status_code == 200 == second.status_code
    assert first.json()["task_id"] != second.json()["task_id"]
    assert len(namespace["TASKS"]) == 2
    assert namespace["IDEMPOTENCY_KEYS"] == {}


def test_non_background_endpoint_has_no_max_pending_tasks_check():
    """A synchronous endpoint never touches TASKS at all -- the check
    only belongs in a background endpoint's own body (or the always-
    present /tasks/{task_id}/retry, which -- like a background
    submission -- also admits a brand-new task into TASKS and so needs
    the identical guard; scoped out below since it isn't specific to
    this notebook's own "add" endpoint).
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    add_endpoint_start = code.index("def add(")
    add_endpoint_body = code[add_endpoint_start:]

    assert "if len(TASKS) >= MAX_PENDING_TASKS:" not in add_endpoint_body
    # The constant itself is still always defined at module level.
    assert "MAX_PENDING_TASKS = int(os.getenv(" in code


def test_notebook_function_named_max_pending_tasks_is_rejected():
    """MAX_PENDING_TASKS is a module-level name the generated app itself
    defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision hazard
    class as TASKS, API_KEYS, or MAX_REQUEST_BODY_BYTES.
    """

    functions = [
        {"name": "MAX_PENDING_TASKS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="MAX_PENDING_TASKS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_tasks_admission_lock_is_rejected():
    """_TASKS_ADMISSION_LOCK -- the threading.Lock a background
    endpoint's own submission code and retry_task both use to make their
    shared "evict, check MAX_PENDING_TASKS, insert" sequence atomic
    across concurrent worker threads -- is read by name from inside both,
    resolved at call time. A notebook function of this exact name would
    rebind it to a function object at module-execution time; the very
    next background submission would then fail with a bare 500 the
    moment `with _TASKS_ADMISSION_LOCK:` tried to use a function object
    as a context manager -- the identical "one bad name breaks a
    subsystem every background endpoint depends on" exposure
    MAX_PENDING_TASKS' own entry above already guards against.
    """

    functions = [
        {"name": "_TASKS_ADMISSION_LOCK", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_TASKS_ADMISSION_LOCK"):
        generate_fastapi_code(functions)


def test_notebook_function_named_webhook_timeout_seconds_is_rejected():
    """WEBHOOK_TIMEOUT_SECONDS is a module-level name the generated app
    itself defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision
    hazard class as MAX_PENDING_TASKS or TASK_TTL_SECONDS.
    """

    functions = [
        {"name": "WEBHOOK_TIMEOUT_SECONDS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="WEBHOOK_TIMEOUT_SECONDS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_webhook_secret_is_rejected():
    """WEBHOOK_SECRET is a module-level name the generated app itself
    defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision hazard
    class as WEBHOOK_TIMEOUT_SECONDS or MAX_PENDING_TASKS.
    """

    functions = [
        {"name": "WEBHOOK_SECRET", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="WEBHOOK_SECRET"):
        generate_fastapi_code(functions)


def test_notebook_function_named_webhook_max_retries_is_rejected():
    """WEBHOOK_MAX_RETRIES is a module-level name the generated app itself
    defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same collision hazard
    class as WEBHOOK_TIMEOUT_SECONDS or WEBHOOK_SECRET.
    """

    functions = [
        {"name": "WEBHOOK_MAX_RETRIES", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="WEBHOOK_MAX_RETRIES"):
        generate_fastapi_code(functions)


def test_notebook_function_named_webhook_retry_backoff_seconds_is_rejected():
    """WEBHOOK_RETRY_BACKOFF_SECONDS is a module-level name the generated
    app itself defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same
    collision hazard class as WEBHOOK_MAX_RETRIES.
    """

    functions = [
        {"name": "WEBHOOK_RETRY_BACKOFF_SECONDS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(
        ReservedFunctionNameError, match="WEBHOOK_RETRY_BACKOFF_SECONDS"
    ):
        generate_fastapi_code(functions)


def test_notebook_function_named_deliver_task_webhook_is_rejected():
    """_deliver_task_webhook is a module-level helper the generated app
    itself defines -- same collision hazard class as _evict_expired_tasks
    or _run_background_task: a notebook function of this exact name would
    silently overwrite the real helper at module-execution time.
    """

    functions = [
        {"name": "_deliver_task_webhook", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_deliver_task_webhook"):
        generate_fastapi_code(functions)


def test_background_endpoint_rejects_non_http_callback_url(monkeypatch):
    """A caller-supplied callback_url is fully caller-controlled input
    (unlike every other limit this app enforces, which is set by the
    operator) -- restricted to http(s) so a "file://" or other
    non-network scheme can't reach urllib.request.urlopen inside
    _deliver_task_webhook at all.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    rejected = client.post(
        "/process_data",
        json={},
        params={"callback_url": "file:///etc/passwd"},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert "callback_url" in rejected.json()["detail"]
    assert namespace["TASKS"] == {}

    accepted = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert accepted.status_code == 200


def test_background_endpoint_rejects_a_private_callback_url_host(monkeypatch):
    """The scheme check above stops "file://", but a perfectly valid
    http(s) URL can still name a host only this app's own network can
    reach. Confirmed exploitable before this fix: nothing here ever
    resolved callback_url's own hostname, so a caller could point a
    background endpoint's webhook delivery at this app's own loopback
    interface -- reaching whatever else happens to be listening there --
    with no error at all.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    rejected = client.post(
        "/process_data",
        json={},
        params={"callback_url": "http://127.0.0.1:9999/hook"},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert "non-public" in rejected.json()["detail"]
    assert namespace["TASKS"] == {}


def test_background_endpoint_rejects_a_cloud_metadata_callback_url_host(monkeypatch):
    """169.254.169.254 is link-local -- the address every major cloud
    provider's own instance-metadata service (IAM credentials included)
    listens on, reachable only from inside that instance's own network.
    The single most realistic real-world target this guard exists to
    keep a caller-supplied callback_url away from.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    rejected = client.post(
        "/process_data",
        json={},
        params={"callback_url": "http://169.254.169.254/latest/meta-data/"},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert "non-public" in rejected.json()["detail"]
    assert namespace["TASKS"] == {}


def test_background_endpoint_rejects_an_unresolvable_callback_url_host(monkeypatch):
    """A hostname that can't be resolved at all is treated the same as a
    private one -- there's no useful "will fail to deliver anyway"
    distinction worth making at validation time, and treating it as safe
    would need every call site to separately handle a resolution failure
    that never actually happens.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    rejected = client.post(
        "/process_data",
        json={},
        params={"callback_url": "http://this-host-does-not-exist.invalid/hook"},
        headers=headers,
    )
    assert rejected.status_code == 400
    assert "non-public" in rejected.json()["detail"]
    assert namespace["TASKS"] == {}


def test_deliver_task_webhook_refuses_a_callback_url_that_now_resolves_privately(
    monkeypatch,
):
    """_is_unsafe_webhook_host is re-checked inside _deliver_task_webhook
    itself, not just once at submission time -- the only way a caller
    could ever observe that is a callback_url that passed the submission-
    time check but no longer resolves safely by the time delivery
    actually happens (DNS rebinding, or the record simply changing).
    Simulated here by mutating an already-submitted task's own stored
    callback_url directly (bypassing the submission-time check
    entirely, the same way a real DNS change would) and then triggering
    POST /tasks/{task_id}/redeliver-webhook -- confirmed refused, and
    confirmed urlopen is never even reached.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    urlopen_calls = []

    class _FakeResponse:
        status = 204

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        urlopen_calls.append(request.full_url)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]
    assert urlopen_calls == ["https://example.test/hook"]

    namespace["TASKS"][task_id]["callback_url"] = "http://127.0.0.1:9999/hook"

    redeliver_response = client.post(
        f"/tasks/{task_id}/redeliver-webhook", headers=headers
    )

    assert redeliver_response.status_code == 200
    assert redeliver_response.json()["webhook"] == {
        "delivered": False,
        "attempts": 0,
        "status_code": None,
        "error": (
            "callback_url resolves to a non-public address; refusing to "
            "deliver"
        ),
    }
    # Delivery for the private host was refused before ever calling
    # urlopen -- still just the one call from the original, safe delivery
    # above.
    assert urlopen_calls == ["https://example.test/hook"]


def test_background_task_delivers_webhook_on_completion_and_failure(monkeypatch):
    """Confirmed missing before this feature: a caller of a background
    endpoint had no way to learn a task finished short of polling
    get_task/wait_for_task -- there was no way to opt into being notified
    the moment it actually completes or fails.
    """
    import urllib.request

    functions = [
        {"name": "process_data", "args": [], "return_type": "dict"},
        {"name": "train_model", "args": [], "return_type": "dict"},
    ]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: {"score": 0.9}

    def _blows_up():
        raise ValueError("training diverged")

    namespace["notebook_module"].train_model = _blows_up

    delivered = []

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        delivered.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    ok_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook-ok"},
        headers=headers,
    )
    assert ok_response.status_code == 200

    fail_response = client.post(
        "/train_model",
        json={},
        params={"callback_url": "https://example.test/hook-fail"},
        headers=headers,
    )
    assert fail_response.status_code == 200

    assert len(delivered) == 2

    ok_call = next(c for c in delivered if c["url"] == "https://example.test/hook-ok")
    assert ok_call["timeout"] == 5
    assert ok_call["body"]["status"] == "completed"
    assert ok_call["body"]["result"] == {"score": 0.9}
    assert ok_call["body"]["task_id"] == ok_response.json()["task_id"]

    fail_call = next(
        c for c in delivered if c["url"] == "https://example.test/hook-fail"
    )
    assert fail_call["body"]["status"] == "failed"
    assert "training diverged" in fail_call["body"]["error"]


def test_background_task_without_callback_url_never_touches_urlopen(monkeypatch):
    """The overwhelmingly common case (no callback_url given) must behave
    exactly as before this feature -- no network call attempted at all.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("urlopen should never be called without callback_url")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/process_data", json={}, headers=headers)
    assert response.status_code == 200


def test_webhook_delivery_failure_does_not_affect_task_result(monkeypatch):
    """Webhook delivery is purely best-effort -- an unreachable/erroring
    callback_url must never prevent the task's own real result from being
    recorded in TASKS.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    def broken_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", broken_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/unreachable"},
        headers=headers,
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    task = namespace["TASKS"][task_id]
    assert task["status"] == "completed"
    assert task["result"] == "ok"


def test_webhook_delivery_omits_signature_header_when_no_secret_configured(
    monkeypatch
):
    """The overwhelmingly common case (NOTEBOOK_API_WEBHOOK_SECRET unset)
    must behave exactly as before this feature -- no signature header at
    all, so an existing receiver that predates this feature keeps working
    unmodified.
    """
    import urllib.request

    monkeypatch.delenv("NOTEBOOK_API_WEBHOOK_SECRET", raising=False)

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    captured = {}

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200

    assert captured["request"].get_header("X-webhook-signature") is None


def test_webhook_delivery_includes_hmac_signature_when_secret_configured(
    monkeypatch
):
    """Confirmed missing before this feature: a receiver of a background
    task's own webhook delivery had no way to verify a request actually
    came from this app (rather than an attacker who guessed or leaked the
    callback_url) -- NOTEBOOK_API_WEBHOOK_SECRET, when set, now signs the
    exact request body with HMAC-SHA256, the same X-Hub-Signature-256
    contract GitHub/Stripe webhooks already use.
    """
    import hashlib
    import hmac
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_SECRET", "s3cr3t")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: {"score": 0.9}

    captured = {}

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200

    request = captured["request"]
    # urllib.request.Request.get_header does an exact lookup against its
    # own stored key, which add_header/the constructor already normalize
    # via str.capitalize() ("X-Webhook-Signature" -> "X-webhook-signature")
    # -- not a case-insensitive lookup, so the header name here must match
    # that exact casing.
    signature_header = request.get_header("X-webhook-signature")
    assert signature_header is not None

    expected_signature = "sha256=" + hmac.new(
        b"s3cr3t", request.data, hashlib.sha256
    ).hexdigest()
    assert signature_header == expected_signature


def test_webhook_delivery_signature_changes_when_secret_changes(monkeypatch):
    """A different NOTEBOOK_API_WEBHOOK_SECRET must produce a different
    signature over the exact same body -- otherwise the secret wouldn't
    actually be doing any verification work.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    def _deliver_with_secret(secret):
        monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_SECRET", secret)

        code = generate_fastapi_code(functions)

        _register_fake_notebook_module(monkeypatch)
        namespace = {}
        exec(compile(code, "<generated>", "exec"), namespace)
        namespace["notebook_module"].process_data = lambda: "ok"

        captured = {}

        class _FakeResponse:
            status = 200

            def close(self):
                pass

        def fake_urlopen(request, timeout=None):
            captured["request"] = request
            return _FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        from fastapi.testclient import TestClient

        client = TestClient(namespace["app"])
        headers = {"X-API-Key": "notebook-to-api-dev-key"}

        client.post(
            "/process_data",
            json={},
            params={"callback_url": "https://example.test/hook"},
            headers=headers,
        )

        return captured["request"].get_header("X-webhook-signature")

    signature_a = _deliver_with_secret("secret-a")
    signature_b = _deliver_with_secret("secret-b")

    assert signature_a != signature_b


def test_webhook_delivery_default_max_retries_is_zero_makes_exactly_one_attempt(
    monkeypatch
):
    """NOTEBOOK_API_WEBHOOK_MAX_RETRIES defaults to 0 -- a single
    best-effort attempt, exactly the behavior this app had before retries
    existed at all. A failing delivery must not be retried by default.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    attempts = []

    def always_fails(request, timeout=None):
        attempts.append(request)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", always_fails)
    monkeypatch.setattr("time.sleep", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("time.sleep should never be called with 0 retries")
    ))

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/unreachable"},
        headers=headers,
    )
    assert response.status_code == 200
    assert len(attempts) == 1


def test_webhook_delivery_retries_a_transient_network_error_then_succeeds(
    monkeypatch
):
    """NOTEBOOK_API_WEBHOOK_MAX_RETRIES > 0 must actually retry a
    connection-level failure (the identical class of transient failure a
    real receiving endpoint's own restart/deploy/overload would produce)
    instead of giving up on the first attempt.
    """
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "2")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    attempts = []

    def flaky_then_ok(request, timeout=None):
        attempts.append(request)
        if len(attempts) < 3:
            raise urllib.error.URLError("connection refused")
        return _FakeResponse()

    sleeps = []

    monkeypatch.setattr(urllib.request, "urlopen", flaky_then_ok)
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200
    assert len(attempts) == 3
    # Exponential backoff, doubling per attempt from the default base of
    # 0.5s (0.5, 1.0) -- only two sleeps since the third attempt succeeds.
    assert sleeps == [0.5, 1.0]


def test_webhook_delivery_gives_up_after_max_retries_exhausted(monkeypatch):
    """A callback_url that never succeeds must still eventually give up --
    exactly NOTEBOOK_API_WEBHOOK_MAX_RETRIES retries, never an infinite
    loop -- and, like every other webhook failure, must never affect the
    task's own real recorded result.
    """
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "2")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    attempts = []

    def always_fails(request, timeout=None):
        attempts.append(request)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", always_fails)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/unreachable"},
        headers=headers,
    )
    assert response.status_code == 200
    # 1 initial attempt + 2 retries.
    assert len(attempts) == 3

    task_id = response.json()["task_id"]
    task = namespace["TASKS"][task_id]
    assert task["status"] == "completed"
    assert task["result"] == "ok"


def test_task_record_reports_successful_webhook_delivery(monkeypatch):
    """Confirmed missing before this feature: _deliver_task_webhook never
    reported whether delivery actually succeeded anywhere a caller could
    see it -- a receiver being unreachable and a receiver working fine
    were indistinguishable from the task's own record. A successful
    delivery must now be recorded on the task itself, reachable via GET
    /tasks/{task_id}, not just swallowed inside the worker thread that
    performed it.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    class _FakeResponse:
        status = 204

        def close(self):
            pass

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: _FakeResponse()
    )

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert submit_response.status_code == 200
    task_id = submit_response.json()["task_id"]

    get_response = client.get(f"/tasks/{task_id}", headers=headers)
    assert get_response.status_code == 200
    webhook = get_response.json()["webhook"]
    assert webhook == {
        "delivered": True,
        "attempts": 1,
        "status_code": 204,
        "error": None,
    }


def test_task_record_reports_failed_webhook_delivery(monkeypatch):
    """The inverse of the success case above -- a callback_url that never
    succeeds must record exactly what went wrong (and how many attempts
    were made) on the task itself, rather than the caller having no way
    to tell a delivery failure apart from one that simply hasn't happened
    yet.
    """
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "2")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    def always_fails(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", always_fails)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/unreachable"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]

    task = namespace["TASKS"][task_id]
    webhook = task["webhook"]
    assert webhook["delivered"] is False
    # 1 initial attempt + 2 retries.
    assert webhook["attempts"] == 3
    assert webhook["status_code"] is None
    assert "connection refused" in webhook["error"]
    # A failed webhook delivery must never demote the task's own real,
    # already-recorded result/status -- it's a separate field entirely.
    assert task["status"] == "completed"
    assert task["result"] == "ok"


def test_task_record_reports_webhook_delivery_status_code_from_non_retryable_error(
    monkeypatch
):
    """A non-retryable 4xx (the receiver deliberately rejecting the
    request) must still surface its real status_code on the task record,
    not None -- that's exactly the detail an operator debugging a
    misconfigured receiver (wrong auth, wrong path) needs.
    """
    import urllib.error
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    def rejects_with_404(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", rejects_with_404)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]

    webhook = namespace["TASKS"][task_id]["webhook"]
    assert webhook == {
        "delivered": False,
        "attempts": 1,
        "status_code": 404,
        "error": webhook["error"],
    }
    assert "404" in webhook["error"]


def test_task_record_reports_webhook_attempts_after_retry_then_success(monkeypatch):
    """attempts on the recorded webhook status must reflect the real
    number of tries made, including retries that failed before the
    eventual success -- not just "1", which would misrepresent every
    delivery that needed a retry as if it had gone through cleanly on the
    first try.
    """
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "2")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    attempts = []

    def flaky_then_ok(request, timeout=None):
        attempts.append(request)
        if len(attempts) < 2:
            raise urllib.error.URLError("connection refused")
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", flaky_then_ok)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]

    webhook = namespace["TASKS"][task_id]["webhook"]
    assert webhook["delivered"] is True
    assert webhook["attempts"] == 2
    assert webhook["status_code"] == 200


def test_deliver_task_webhook_re_checks_host_safety_before_each_retry(monkeypatch):
    """_is_unsafe_webhook_host was previously only ever checked once,
    immediately before the retry loop starts -- confirmed exploitable
    before this fix: a caller-controlled DNS record (the callback_url's
    own domain) that resolves safely for that one check but starts
    resolving somewhere unsafe by the time a *later* retry's own
    urlopen call independently re-resolves it (classic DNS rebinding,
    across the exponential-backoff/Retry-After sleep between attempts)
    was never caught -- only a *separate*, later top-level call to this
    same function (a manual redeliver, or a retried task's own eventual
    completion) ever re-checked at all, not a retry within one
    already-in-progress call's own loop.

    Simulated here by making _is_unsafe_webhook_host itself return
    False for the pre-loop check and the first delivery attempt, then
    True from the second attempt onward -- the same shape a live DNS
    rebinding attack produces (the *same* callback_url resolving safely,
    then unsafely, at two different times) -- while urlopen's own first
    call fails with a retryable network error, forcing exactly the
    retry this check must catch. Confirmed urlopen is never reached a
    second time.
    """
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "2")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    host_check_calls = []

    def fake_is_unsafe_webhook_host(url):
        host_check_calls.append(url)
        # Two checks happen before the retry loop's own first real
        # attempt: the endpoint's own submission-time check, then
        # _deliver_task_webhook's own pre-loop check. The third call is
        # attempt > 0's own re-check, right before what would otherwise
        # be the retry's own urlopen call -- this is the one this fix
        # adds, and the one this test means to catch.
        return len(host_check_calls) >= 3

    namespace["_is_unsafe_webhook_host"] = fake_is_unsafe_webhook_host

    urlopen_calls = []

    def flaky_urlopen(request, timeout=None):
        urlopen_calls.append(request.full_url)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", flaky_urlopen)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]

    webhook = namespace["TASKS"][task_id]["webhook"]
    assert webhook["delivered"] is False
    assert "non-public address" in webhook["error"]
    # Exactly one real network attempt: the first, allowed through by
    # the fake's own first (safe) verdict; the retry that would have
    # followed the URLError is blocked before ever reaching urlopen
    # again.
    assert len(urlopen_calls) == 1
    assert len(host_check_calls) == 3


def test_task_record_omits_webhook_field_when_no_callback_url_given(monkeypatch):
    """The overwhelmingly common case (no callback_url) must gain no new
    field at all -- "webhook" only appears on a task record when a
    delivery was actually attempted.
    """
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post("/process_data", json={}, headers=headers)
    task_id = submit_response.json()["task_id"]

    assert "webhook" not in namespace["TASKS"][task_id]


def test_metrics_reports_webhook_delivery_outcomes(monkeypatch):
    """Confirmed missing before this feature: a delivery's own outcome
    was recorded only on that one task's own TASKS[task_id]['webhook']
    field -- nothing aggregated it anywhere, so a caller relying on
    webhooks instead of polling had no way to notice "my delivery
    failure rate just spiked" short of polling every task individually
    and counting failures by hand.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    class _FakeResponse:
        status = 204

        def close(self):
            pass

    calls = {"n": 0}

    def flaky_urlopen(request, timeout=None):
        calls["n"] += 1
        # First delivery succeeds, second fails outright -- one of each
        # outcome, so the assertions below can't pass by accident (e.g.
        # both landing in the same bucket).
        if calls["n"] == 1:
            return _FakeResponse()
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", flaky_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    for _ in range(2):
        client.post(
            "/process_data",
            json={},
            params={"callback_url": "https://example.test/hook"},
            headers=headers,
        )

    body = client.get("/metrics").json()
    assert body["webhook_deliveries_by_outcome"] == {"delivered": 1, "failed": 1}
    # No manual redelivery ever happened -- must stay untouched.
    assert body["webhook_redeliveries_by_outcome"] == {"delivered": 0, "failed": 0}


def test_metrics_prometheus_reports_webhook_delivery_outcomes(monkeypatch):
    """Mirrors test_metrics_reports_webhook_delivery_outcomes for the
    Prometheus text exposition endpoint.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    def always_fails(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", always_fails)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )

    body = client.get("/metrics/prometheus").text

    assert "# HELP notebook_api_webhook_deliveries_total" in body
    assert "# TYPE notebook_api_webhook_deliveries_total counter" in body
    assert 'notebook_api_webhook_deliveries_total{outcome="delivered"} 0' in body
    assert 'notebook_api_webhook_deliveries_total{outcome="failed"} 1' in body
    assert "# HELP notebook_api_webhook_redeliveries_total" in body
    assert "# TYPE notebook_api_webhook_redeliveries_total counter" in body
    assert 'notebook_api_webhook_redeliveries_total{outcome="delivered"} 0' in body
    assert 'notebook_api_webhook_redeliveries_total{outcome="failed"} 0' in body


def test_metrics_reports_manual_webhook_redelivery_outcomes(monkeypatch):
    """A manual POST /tasks/{task_id}/redeliver-webhook must be counted
    separately from the automatic delivery it's retrying -- folding both
    into one counter would make "my receiver is flaky enough that I keep
    having to redeliver" indistinguishable from "deliveries just keep
    failing outright", two different signals an operator would want to
    tell apart.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    def always_fails(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", always_fails)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]

    # The automatic delivery above already failed once -- confirm it
    # landed in 'failed', not 'redelivery_failed'.
    body = client.get("/metrics").json()
    assert body["webhook_deliveries_by_outcome"] == {"delivered": 0, "failed": 1}
    assert body["webhook_redeliveries_by_outcome"] == {"delivered": 0, "failed": 0}

    class _FakeResponse:
        status = 204

        def close(self):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: _FakeResponse())

    redeliver_response = client.post(
        f"/tasks/{task_id}/redeliver-webhook", headers=headers
    )
    assert redeliver_response.status_code == 200

    body = client.get("/metrics").json()
    # The original failed automatic delivery must stay exactly as it was.
    assert body["webhook_deliveries_by_outcome"] == {"delivered": 0, "failed": 1}
    # The manual redelivery succeeded -- counted in its own bucket.
    assert body["webhook_redeliveries_by_outcome"] == {"delivered": 1, "failed": 0}


def test_notebook_function_named_webhook_metrics_is_rejected():
    """_WEBHOOK_METRICS is read and written by name from inside
    _run_background_task's own three webhook-delivery call sites and
    redeliver_task_webhook's own body, then read again by name from
    metrics()/metrics_prometheus() -- the identical collision hazard
    _HTTP_METRICS' own reserved-name test already covers, just for this
    dict instead.
    """
    functions = [
        {"name": "_WEBHOOK_METRICS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="_WEBHOOK_METRICS"):
        generate_fastapi_code(functions)


def test_notebook_function_named_redeliver_task_webhook_is_rejected():
    """redeliver_task_webhook is the endpoint function POST
    /tasks/{task_id}/redeliver-webhook is defined as, a module-level name
    like every other endpoint function (get_task, delete_task, ...)
    already reserved above -- a notebook function of this exact name
    would silently replace the real endpoint at module-execution time.
    """

    functions = [
        {"name": "redeliver_task_webhook", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="redeliver_task_webhook"):
        generate_fastapi_code(functions)


def test_redeliver_webhook_resends_a_completed_tasks_recorded_result(monkeypatch):
    """POST /tasks/{task_id}/redeliver-webhook must resend the task's own
    already-recorded outcome to the exact callback_url it was originally
    submitted with, without re-running the notebook function itself --
    confirmed by making the notebook function a one-shot: if this
    endpoint re-executed it, the second call would raise.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    calls = {"count": 0}

    def one_shot():
        calls["count"] += 1
        if calls["count"] > 1:
            raise AssertionError("notebook function must not be re-run")
        return "ok"

    namespace["notebook_module"].process_data = one_shot

    requests_seen = []

    class _FakeResponse:
        status = 204

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        requests_seen.append(json.loads(request.data))
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]
    assert len(requests_seen) == 1

    redeliver_response = client.post(
        f"/tasks/{task_id}/redeliver-webhook", headers=headers
    )
    assert redeliver_response.status_code == 200
    body = redeliver_response.json()
    assert body["task_id"] == task_id
    assert body["webhook"] == {
        "delivered": True,
        "attempts": 1,
        "status_code": 204,
        "error": None,
    }
    assert body["webhook_redelivery_count"] == 1

    assert len(requests_seen) == 2
    assert requests_seen[1] == {
        "task_id": task_id,
        "status": "completed",
        "result": "ok",
    }

    task = namespace["TASKS"][task_id]
    assert task["webhook_redelivery_count"] == 1
    assert task["status"] == "completed"
    assert task["result"] == "ok"

    # A second redelivery keeps incrementing the same counter.
    second_response = client.post(
        f"/tasks/{task_id}/redeliver-webhook", headers=headers
    )
    assert second_response.json()["webhook_redelivery_count"] == 2
    assert len(requests_seen) == 3


def test_redeliver_webhook_resends_a_failed_tasks_recorded_error(monkeypatch):
    """The inverse of the completed case -- a failed task's own recorded
    error, not its (nonexistent) result, must be what gets redelivered.
    """
    import urllib.request

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    def always_raises():
        raise ValueError("boom")

    namespace["notebook_module"].process_data = always_raises

    requests_seen = []

    class _FakeResponse:
        status = 204

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        requests_seen.append(json.loads(request.data))
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    task_id = submit_response.json()["task_id"]

    redeliver_response = client.post(
        f"/tasks/{task_id}/redeliver-webhook", headers=headers
    )
    assert redeliver_response.status_code == 200
    assert requests_seen[-1] == {
        "task_id": task_id,
        "status": "failed",
        "error": "boom",
    }


def test_redeliver_webhook_404s_for_an_unknown_task(monkeypatch):
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/tasks/does-not-exist/redeliver-webhook", headers=headers)
    assert response.status_code == 404


def test_redeliver_webhook_409s_while_the_task_is_still_processing(monkeypatch):
    """There is no recorded result or error to redeliver yet -- the same
    'still processing' rejection DELETE /tasks/{task_id} already applies.
    """
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["TASKS"]["still-running"] = {
        "status": "processing",
        "created_at": 0,
        "callback_url": "https://example.test/hook",
    }

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/tasks/still-running/redeliver-webhook", headers=headers
    )
    assert response.status_code == 409


def test_redeliver_webhook_400s_when_task_had_no_callback_url(monkeypatch):
    """A task submitted without ?callback_url= never had a webhook to
    begin with -- nothing to redeliver, and no URL to redeliver it to.
    """
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post("/process_data", json={}, headers=headers)
    task_id = submit_response.json()["task_id"]

    response = client.post(
        f"/tasks/{task_id}/redeliver-webhook", headers=headers
    )
    assert response.status_code == 400
    assert "callback_url" in response.json()["detail"]


def test_notebook_function_named_retry_task_is_rejected():
    """retry_task is the endpoint function POST /tasks/{task_id}/retry is
    defined as -- reserved the same way redeliver_task_webhook already is.
    """

    functions = [
        {"name": "retry_task", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="retry_task"):
        generate_fastapi_code(functions)


def test_retry_resubmits_a_failed_task_with_its_original_inputs(monkeypatch):
    """POST /tasks/{task_id}/retry must re-run the notebook function --
    unlike redeliver-webhook, which deliberately never does -- using the
    exact positional and keyword-only arguments it was originally
    submitted with, under a brand-new task_id. The original, still-failed
    task must be left untouched.
    """

    functions = [
        {
            "name": "process_data",
            "args": [
                {"name": "x", "type": "int", "kind": "positional"},
                {
                    "name": "epochs", "type": "int", "default": 10,
                    "has_default": True, "kind": "keyword_only",
                },
            ],
            "return_type": "dict",
        }
    ]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    calls = []

    def flaky(x, *, epochs=10):
        calls.append((x, epochs))
        if len(calls) == 1:
            raise ValueError("boom")
        return x * epochs

    namespace["notebook_module"].process_data = flaky

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post(
        "/process_data", json={"x": 3, "epochs": 4}, headers=headers
    )
    task_id = submit_response.json()["task_id"]
    assert namespace["TASKS"][task_id]["status"] == "failed"
    assert calls == [(3, 4)]

    retry_response = client.post(f"/tasks/{task_id}/retry", headers=headers)
    assert retry_response.status_code == 200
    body = retry_response.json()
    new_task_id = body["task_id"]
    assert new_task_id != task_id
    assert body["status"] == "processing"
    assert body["retried_from"] == task_id

    assert calls == [(3, 4), (3, 4)]
    new_task = namespace["TASKS"][new_task_id]
    assert new_task["status"] == "completed"
    assert new_task["result"] == 12
    assert new_task["retried_from"] == task_id

    # The original failed task is untouched by the retry.
    assert namespace["TASKS"][task_id]["status"] == "failed"
    assert namespace["TASKS"][task_id]["error"] == "boom"


def test_retry_404s_for_an_unknown_task(monkeypatch):
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/tasks/does-not-exist/retry", headers=headers)
    assert response.status_code == 404


def test_retry_409s_for_a_task_that_is_still_processing_or_already_completed(monkeypatch):
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["TASKS"]["still-running"] = {
        "status": "processing", "created_at": 0, "callback_url": None,
    }
    namespace["TASKS"]["already-done"] = {
        "status": "completed", "created_at": 0, "callback_url": None,
        "result": "ok",
        "_replay": {"func_name": "process_data", "args": (), "kwargs": {}},
    }

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/tasks/still-running/retry", headers=headers)
    assert response.status_code == 409

    response = client.post("/tasks/already-done/retry", headers=headers)
    assert response.status_code == 409


def test_retry_409s_when_task_has_no_recorded_replay_inputs(monkeypatch):
    """A failed task created before this endpoint existed (or manually
    injected, as here) has no '_replay' -- there is nothing to retry it
    with, distinct from the 404/'still processing' cases above.
    """
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["TASKS"]["legacy-failure"] = {
        "status": "failed", "created_at": 0, "callback_url": None,
        "error": "boom",
    }

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/tasks/legacy-failure/retry", headers=headers)
    assert response.status_code == 409
    assert "no recorded inputs" in response.json()["detail"]


def test_get_task_and_list_tasks_never_expose_the_internal_replay_field(monkeypatch):
    """'_replay' (see the retry tests above) is internal bookkeeping for
    POST /tasks/{task_id}/retry -- it must never leak out through either
    of this app's own two task-reading endpoints.
    """
    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    def always_raises():
        raise ValueError("boom")

    namespace["notebook_module"].process_data = always_raises

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    submit_response = client.post("/process_data", json={}, headers=headers)
    task_id = submit_response.json()["task_id"]
    assert "_replay" in namespace["TASKS"][task_id]

    get_response = client.get(f"/tasks/{task_id}", headers=headers)
    assert "_replay" not in get_response.json()

    list_response = client.get("/tasks", headers=headers)
    assert "_replay" not in list_response.json()["tasks"][task_id]


def test_webhook_delivery_retries_a_5xx_http_error_then_succeeds(monkeypatch):
    """A 5xx response from the receiving endpoint is the same class of
    transient failure a connection-level error already retries -- the
    receiver is (or was, at the moment of that response) having its own
    problem, not deliberately rejecting this request.
    """
    import urllib.error
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "1")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    attempts = []

    def fails_once_with_503(request, timeout=None):
        attempts.append(request)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 503, "Service Unavailable", {}, None
            )
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fails_once_with_503)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200
    assert len(attempts) == 2


def test_webhook_delivery_does_not_retry_a_non_retryable_4xx_http_error(
    monkeypatch
):
    """A 4xx response other than 429 means the receiver deliberately
    rejected this exact request -- retrying an unchanged body against it
    again can only ever fail the same way, so this must never retry it
    even with retries otherwise enabled.
    """
    import urllib.error
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "3")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    attempts = []

    def rejects_with_404(request, timeout=None):
        attempts.append(request)
        raise urllib.error.HTTPError(
            request.full_url, 404, "Not Found", {}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", rejects_with_404)
    monkeypatch.setattr("time.sleep", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a non-retryable 4xx must never be retried")
    ))

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200
    assert len(attempts) == 1


def test_webhook_delivery_honors_retry_after_header_on_429(monkeypatch):
    """A 429 response's own Retry-After header (seconds form) must drive
    the wait before the next attempt instead of the computed exponential
    backoff -- the receiving endpoint told this app exactly how long to
    wait, the identical "honor Retry-After instead of guessing" behavior
    the generated SDK clients' own retry logic already gives a 429.
    """
    import urllib.error
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_MAX_RETRIES", "1")
    monkeypatch.setenv("NOTEBOOK_API_WEBHOOK_RETRY_BACKOFF_SECONDS", "0.5")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].process_data = lambda: "ok"

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    attempts = []

    def fails_once_with_429(request, timeout=None):
        attempts.append(request)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests",
                {"Retry-After": "12"}, None,
            )
        return _FakeResponse()

    sleeps = []

    monkeypatch.setattr(urllib.request, "urlopen", fails_once_with_429)
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/process_data",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200
    assert len(attempts) == 2
    # 12s from Retry-After, not the computed 0.5s exponential backoff.
    assert sleeps == [12.0]


def test_notebook_function_named_task_execution_timeout_seconds_is_rejected():
    """TASK_EXECUTION_TIMEOUT_SECONDS is a module-level name the generated
    app itself defines (see RESERVED_INFRASTRUCTURE_NAMES) -- same
    collision hazard class as WEBHOOK_TIMEOUT_SECONDS or MAX_PENDING_TASKS.
    """

    functions = [
        {"name": "TASK_EXECUTION_TIMEOUT_SECONDS", "args": [], "return_type": "dict"}
    ]

    with pytest.raises(ReservedFunctionNameError, match="TASK_EXECUTION_TIMEOUT_SECONDS"):
        generate_fastapi_code(functions)


def test_background_sync_task_hanging_past_the_timeout_is_marked_failed(monkeypatch):
    """Confirmed exploitable before this feature: a hung or runaway
    notebook function (an infinite loop, a network call with no timeout
    of its own) tied up one of this process' limited worker threads
    forever -- the same threadpool every *synchronous* endpoint (GET
    /health included) also runs on, so enough hung tasks would eventually
    starve the entire app, not just background ones.
    """
    import time as time_module

    monkeypatch.setenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", "1")

    functions = [{"name": "train_model", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: time_module.sleep(30)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    start = time_module.monotonic()
    response = client.post("/train_model", json={}, headers=headers)
    elapsed = time_module.monotonic() - start

    assert response.status_code == 200
    # abandon_on_cancel=True is what makes this assertion meaningful: the
    # orphaned thread itself keeps sleeping for the full 30s in the
    # background, but this request -- and the coroutine awaiting it --
    # must not be held up waiting for it.
    assert elapsed < 30

    task_id = response.json()["task_id"]
    task = namespace["TASKS"][task_id]
    assert task["status"] == "failed"
    assert "1s" in task["error"]
    assert "execution timeout" in task["error"]


def test_background_async_task_hanging_past_the_timeout_is_marked_failed(monkeypatch):

    import asyncio

    monkeypatch.setenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", "1")

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    async def _hangs_forever():
        await asyncio.sleep(30)

    namespace["notebook_module"].process_data = _hangs_forever

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/process_data", json={}, headers=headers)

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    task = namespace["TASKS"][task_id]
    assert task["status"] == "failed"
    assert "execution timeout" in task["error"]


def test_background_task_timeout_delivers_a_failed_webhook(monkeypatch):

    import time as time_module
    import urllib.request

    monkeypatch.setenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", "1")

    functions = [{"name": "train_model", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: time_module.sleep(30)

    delivered = []

    class _FakeResponse:
        status = 200

        def close(self):
            pass

    def fake_urlopen(request, timeout=None):
        delivered.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post(
        "/train_model",
        json={},
        params={"callback_url": "https://example.test/hook"},
        headers=headers,
    )
    assert response.status_code == 200

    assert len(delivered) == 1
    assert delivered[0]["status"] == "failed"
    assert "execution timeout" in delivered[0]["error"]

    # The webhook itself was successfully delivered (this fake receiver
    # accepted it) even though the task it's reporting on failed -- the
    # two are independent outcomes, and the recorded webhook status must
    # reflect the delivery, not the task's own result.
    task_id = response.json()["task_id"]
    webhook = namespace["TASKS"][task_id]["webhook"]
    assert webhook == {
        "delivered": True,
        "attempts": 1,
        "status_code": 200,
        "error": None,
    }


def test_background_task_disabled_timeout_preserves_unbounded_execution(monkeypatch):
    """0 (the default) must behave exactly as before this feature existed
    -- a slow-but-finite task still completes normally, never cut off.
    """
    import time as time_module

    monkeypatch.delenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", raising=False)

    functions = [{"name": "train_model", "args": [], "return_type": "str"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["notebook_module"].train_model = lambda: (
        time_module.sleep(0.3) or "done"
    )

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    response = client.post("/train_model", json={}, headers=headers)

    assert response.status_code == 200
    task_id = response.json()["task_id"]
    task = namespace["TASKS"][task_id]
    assert task["status"] == "completed"
    assert task["result"] == "done"


def test_generated_app_get_config_reports_task_execution_timeout_seconds(monkeypatch):

    monkeypatch.setenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", "45")

    functions = [{"name": "add", "args": [], "return_type": "int"}]
    code = generate_fastapi_code(functions)

    assert "'task_execution_timeout_seconds': TASK_EXECUTION_TIMEOUT_SECONDS or None," in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    resp = client.get("/config")

    assert resp.status_code == 200
    assert resp.json()["task_execution_timeout_seconds"] == 45


def test_generated_app_get_config_reports_null_task_execution_timeout_by_default(
    monkeypatch
):

    monkeypatch.delenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", raising=False)

    functions = [{"name": "add", "args": [], "return_type": "int"}]
    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    resp = client.get("/config")

    assert resp.status_code == 200
    assert resp.json()["task_execution_timeout_seconds"] is None


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
    assert "functools.partial(notebook_module.fetch_data, " in code
    assert "is_async=True)" in code


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
    assert "is_async=False)" in code
    assert "await notebook_module.add(" not in code
    assert "functools.partial(notebook_module.add, " in code


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

    assert "functools.partial(notebook_module.score, req.data, epochs=req.epochs)" in code


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

    list_tasks_signature = code[code.index("def list_tasks("):code.index("):", code.index("def list_tasks(")) + 2]
    assert "_: None = Depends(verify_api_key)" in list_tasks_signature
    assert "def get_task(task_id: str, _: None = Depends(verify_api_key)):" in code
    assert "def delete_completed_tasks(_: None = Depends(verify_api_key)):" in code
    assert "def delete_failed_tasks(timed_out: Optional[bool] = None, _: None = Depends(verify_api_key)):" in code
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


def test_list_tasks_supports_status_filter_and_pagination(monkeypatch):
    """Confirmed exploitable before this fix: GET /tasks always returned
    the *entire* TASKS dict with no filter and no pagination -- with
    NOTEBOOK_API_MAX_TASKS defaulting to 10000, and each task potentially
    carrying a large `result` payload, a single request could return an
    enormous response body, and there was no way to ask for just e.g. the
    failed tasks without fetching and filtering client-side.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    assert "status: Optional[str] = None," in code
    assert "limit: int = Query(default=100, ge=1, le=1000)," in code
    assert "offset: int = Query(default=0, ge=0)," in code
    assert "matching_items.sort(key=lambda item: item[1].get('created_at', 0), reverse=True)" in code
    assert "'matching_tasks': len(matching_items)," in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    namespace["TASKS"]["t-old-failed"] = {
        "status": "failed", "created_at": 1.0, "error": "boom",
    }
    namespace["TASKS"]["t-new-completed"] = {
        "status": "completed", "created_at": 3.0, "result": "ok",
    }
    namespace["TASKS"]["t-mid-processing"] = {
        "status": "processing", "created_at": 2.0,
    }

    response = client.get("/tasks", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["matching_tasks"] == 3
    assert body["limit"] == 100
    assert body["offset"] == 0
    # Most recently created task first.
    assert list(body["tasks"].keys()) == [
        "t-new-completed", "t-mid-processing", "t-old-failed",
    ]

    filtered = client.get("/tasks", params={"status": "failed"}, headers=headers)
    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert filtered_body["matching_tasks"] == 1
    assert list(filtered_body["tasks"].keys()) == ["t-old-failed"]

    paginated = client.get(
        "/tasks", params={"limit": 1, "offset": 1}, headers=headers
    )
    assert paginated.status_code == 200
    paginated_body = paginated.json()
    assert paginated_body["matching_tasks"] == 3
    assert list(paginated_body["tasks"].keys()) == ["t-mid-processing"]

    invalid_status = client.get(
        "/tasks", params={"status": "bogus"}, headers=headers
    )
    assert invalid_status.status_code == 400
    assert "Invalid status 'bogus'" in invalid_status.json()["detail"]

    invalid_limit = client.get("/tasks", params={"limit": 0}, headers=headers)
    assert invalid_limit.status_code == 422


def test_task_dict_iterations_survive_concurrent_mutation(monkeypatch):
    """GET /tasks, DELETE /tasks/completed, DELETE /tasks/failed, and
    _task_status_counts (shared by GET /metrics/GET /metrics/prometheus)
    each iterate TASKS directly -- all four are plain synchronous `def`s,
    run in Starlette's own threadpool, while a background task's own
    eventual completion or a concurrent submission's own admission-
    controlled insert (_TASKS_ADMISSION_LOCK) can add or remove a TASKS
    key from a different thread/the event loop at the same moment.
    Confirmed exploitable before this fix: iterating a dict directly
    while another thread changes its own size raises "RuntimeError:
    dictionary changed size during iteration" in CPython -- a real,
    reproducible crash under concurrent read+write task traffic (a
    dashboard polling GET /tasks or /metrics while background tasks are
    actively being submitted or completing), not a silent one. Fixed by
    snapshotting via list(...) before iterating, the standard "safe to
    iterate even if another thread mutates the dict mid-loop" idiom.
    """
    import threading

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    assert "tasks_snapshot = list(TASKS.items())" in code
    assert "tasks_snapshot = list(TASKS.values())" in code
    assert "for task_id, task in list(TASKS.items())" in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    namespace["TASKS"] = {
        str(i): {"status": "completed", "created_at": 0.0} for i in range(50)
    }

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    errors = []

    def mutator():
        for i in range(500):
            key = f"mut{i}"
            namespace["TASKS"][key] = {"status": "processing", "created_at": 0.0}
            namespace["TASKS"].pop(key, None)

    def reader():
        for _ in range(150):
            try:
                assert client.get("/tasks", headers=headers).status_code == 200
                assert (
                    client.delete("/tasks/completed", headers=headers).status_code
                    == 200
                )
                assert (
                    client.delete("/tasks/failed", headers=headers).status_code == 200
                )
                assert client.get("/metrics", headers=headers).status_code == 200
                namespace["TASKS"]["seed"] = {
                    "status": "completed", "created_at": 0.0,
                }
            except RuntimeError as e:
                errors.append(str(e))

    threads = (
        [threading.Thread(target=mutator) for _ in range(3)]
        + [threading.Thread(target=reader) for _ in range(3)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


def test_list_tasks_filters_by_webhook_delivery_failed(monkeypatch):
    """Confirmed missing before this feature: a caller who'd learned from
    GET /metrics that some automatic webhook deliveries had failed had no
    way to find out *which* tasks those were through GET /tasks itself --
    only status filtered the listing, nothing about a task's own
    "webhook" outcome. Before this, that meant fetching every task
    unfiltered and inspecting each one's own "webhook" field by hand just
    to find the ones worth a POST /tasks/{task_id}/redeliver-webhook.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    assert "webhook_delivery_failed: Optional[bool] = None," in code
    assert "'webhook_delivery_failed_tasks': webhook_delivery_failed_tasks," in code

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    namespace["TASKS"]["t-failed-webhook"] = {
        "status": "completed", "created_at": 1.0,
        "webhook": {"delivered": False},
    }
    namespace["TASKS"]["t-delivered-webhook"] = {
        "status": "completed", "created_at": 2.0,
        "webhook": {"delivered": True},
    }
    namespace["TASKS"]["t-no-webhook"] = {
        "status": "completed", "created_at": 3.0,
    }
    namespace["TASKS"]["t-failed-status-and-webhook"] = {
        "status": "failed", "created_at": 4.0,
        "webhook": {"delivered": False},
    }

    unfiltered = client.get("/tasks", headers=headers)
    assert unfiltered.json()["webhook_delivery_failed_tasks"] == 2

    failed_only = client.get(
        "/tasks", params={"webhook_delivery_failed": "true"}, headers=headers
    )
    assert failed_only.status_code == 200
    assert set(failed_only.json()["tasks"].keys()) == {
        "t-failed-webhook", "t-failed-status-and-webhook",
    }
    assert failed_only.json()["matching_tasks"] == 2

    delivered_only = client.get(
        "/tasks", params={"webhook_delivery_failed": "false"}, headers=headers
    )
    assert delivered_only.status_code == 200
    assert list(delivered_only.json()["tasks"].keys()) == ["t-delivered-webhook"]

    # A task with no "webhook" field at all (no callback_url was ever
    # given) must match neither -- it has no delivery outcome to filter
    # by, in either direction.
    assert "t-no-webhook" not in failed_only.json()["tasks"]
    assert "t-no-webhook" not in delivered_only.json()["tasks"]

    # Composes with "status" exactly like every other filter here already
    # does.
    combined = client.get(
        "/tasks",
        params={"status": "failed", "webhook_delivery_failed": "true"},
        headers=headers,
    )
    assert combined.status_code == 200
    assert list(combined.json()["tasks"].keys()) == ["t-failed-status-and-webhook"]


def test_evict_expired_tasks_never_evicts_a_processing_task(monkeypatch):
    """Confirmed exploitable before this fix: _evict_expired_tasks swept
    out any task past TASK_TTL_SECONDS purely by created_at age, with no
    regard for whether it was still 'processing' -- a background function
    (train/process/generate/embed/scrape, routinely slow by design) that
    simply took longer than the TTL to finish had its own TASKS entry
    evicted while still running, out from under it.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    long_ago = namespace["time"].time() - namespace["TASK_TTL_SECONDS"] - 1

    namespace["TASKS"]["still-running"] = {
        "status": "processing", "created_at": long_ago,
    }
    namespace["TASKS"]["long-done"] = {
        "status": "completed", "created_at": long_ago, "result": "ok",
    }

    namespace["_evict_expired_tasks"]()

    assert "still-running" in namespace["TASKS"]
    assert "long-done" not in namespace["TASKS"]


def test_evict_expired_tasks_prunes_idempotency_keys_pointing_at_gone_tasks(
    monkeypatch,
):
    """IDEMPOTENCY_KEYS exists solely to point back at a live TASKS entry
    (see submit_task's own idempotency_key handling) -- without this
    sweep, a key whose task was evicted above (or removed some other way
    entirely -- DELETE /tasks/{task_id}, /tasks/cleanup, /tasks/reset)
    would sit in IDEMPOTENCY_KEYS forever, since nothing else here ever
    prunes it, growing memory without bound on a long-running deployment
    handling steady retried-submission traffic.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    long_ago = namespace["time"].time() - namespace["TASK_TTL_SECONDS"] - 1

    namespace["TASKS"]["still-running"] = {
        "status": "processing", "created_at": long_ago,
    }
    namespace["TASKS"]["long-done"] = {
        "status": "completed", "created_at": long_ago, "result": "ok",
    }
    namespace["IDEMPOTENCY_KEYS"]["key-for-still-running"] = "still-running"
    namespace["IDEMPOTENCY_KEYS"]["key-for-long-done"] = "long-done"
    namespace["IDEMPOTENCY_KEYS"]["key-for-already-gone"] = "never-existed"

    namespace["_evict_expired_tasks"]()

    assert namespace["IDEMPOTENCY_KEYS"] == {"key-for-still-running": "still-running"}


def test_delete_task_rejects_deletion_of_a_processing_task(monkeypatch):
    """Confirmed exploitable before this fix: DELETE /tasks/{task_id}
    popped a task's TASKS entry regardless of its status -- there is no
    way to actually cancel work already handed to a background thread, so
    deleting a still-processing task just meant _run_background_task's
    own eventual TASKS[task_id][...] write raised a bare KeyError once
    the task finished, an unhandled exception silently losing that task's
    real result or error.
    """

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    headers = {"X-API-Key": "notebook-to-api-dev-key"}

    namespace["TASKS"]["still-running"] = {"status": "processing"}
    namespace["TASKS"]["already-done"] = {"status": "completed", "result": "ok"}

    conflict = client.delete("/tasks/still-running", headers=headers)
    assert conflict.status_code == 409
    assert "still processing" in conflict.json()["detail"]
    assert "still-running" in namespace["TASKS"]

    ok = client.delete("/tasks/already-done", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["status"] == "completed"
    assert "already-done" not in namespace["TASKS"]

    missing = client.delete("/tasks/does-not-exist", headers=headers)
    assert missing.status_code == 404


def test_run_background_task_tolerates_a_missing_tasks_entry(monkeypatch):
    """_evict_expired_tasks (above) never removes a 'processing' task, but
    POST /tasks/reset still unconditionally clears every entry, in-flight
    or not. Confirmed exploitable before this fix: a task still running
    when /tasks/reset fired raised a bare KeyError from inside
    _run_background_task once it finished -- an unhandled exception in a
    fire-and-forget asyncio task -- for both the success path
    (TASKS[task_id]["status"] = "completed") and the failure path
    (TASKS[task_id]["error"] = ...), since the second raised again against
    the same missing key.
    """
    import asyncio

    functions = [{"name": "process_data", "args": [], "return_type": "dict"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    run_background_task = namespace["_run_background_task"]

    def succeeds():
        return "ok"

    def fails():
        raise ValueError("boom")

    # Neither task_id was ever added to TASKS -- simulating one removed
    # out from under a still-running task by POST /tasks/reset. Must not
    # raise, and must not resurrect the entry.
    asyncio.run(run_background_task(succeeds, "reset-away-success"))
    asyncio.run(run_background_task(fails, "reset-away-failure"))

    assert "reset-away-success" not in namespace["TASKS"]
    assert "reset-away-failure" not in namespace["TASKS"]


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


def test_sync_directive_overrides_a_long_running_keyword_name_match():
    """"regenerate_token" contains "generate" -- a LONG_RUNNING_KEYWORDS
    match -- but background_overrides explicitly says otherwise.
    """

    functions = [{"name": "regenerate_token", "args": [], "return_type": "str"}]

    code = generate_fastapi_code(
        functions, background_overrides={"regenerate_token": False}
    )

    decorator_line = next(
        line for line in code.splitlines()
        if '@app.post("/regenerate_token"' in line
    )

    assert "x-notebook-to-api-async" not in decorator_line


def test_background_directive_overrides_a_non_matching_name():
    """"run_batch_inference" matches none of LONG_RUNNING_KEYWORDS, but
    background_overrides explicitly says it should be background anyway.
    """

    functions = [{"name": "run_batch_inference", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(
        functions, background_overrides={"run_batch_inference": True}
    )

    decorator_line = next(
        line for line in code.splitlines()
        if '@app.post("/run_batch_inference"' in line
    )

    assert '"x-notebook-to-api-async": True' in decorator_line


def test_background_overrides_with_no_entry_for_a_function_falls_back_to_the_heuristic():

    functions = [
        {"name": "train_model", "args": [], "return_type": "str"},
        {"name": "add", "args": [], "return_type": "int"},
    ]

    code = generate_fastapi_code(
        functions, background_overrides={"unrelated_function": True}
    )

    train_line = next(
        line for line in code.splitlines() if '@app.post("/train_model"' in line
    )
    add_line = next(
        line for line in code.splitlines() if '@app.post("/add"' in line
    )

    assert '"x-notebook-to-api-async": True' in train_line
    assert "x-notebook-to-api-async" not in add_line


def test_deprecated_directive_marks_a_sync_endpoint_deprecated_in_the_openapi_schema(
    monkeypatch,
):
    """Confirmed missing before this feature: a "# notebook-to-api:
    deprecated" directive (_extract_deprecated_functions, backend/
    compiler.py) had no effect anywhere -- generate_fastapi_code never
    set FastAPI's own standard "deprecated": true for any endpoint no
    matter what a notebook author declared, so Swagger UI/Redoc (and any
    third-party tool reading openapi.json) never showed the endpoint as
    deprecated at all.
    """

    functions = [{
        "name": "old_add", "args": [], "return_type": "int",
        "docstring": "Add two numbers.",
    }]

    code = generate_fastapi_code(
        functions,
        deprecated_overrides={"old_add": "Use add_v2 instead."},
    )

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    operation = schema["paths"]["/old_add"]["post"]

    assert operation["deprecated"] is True
    assert operation["description"].startswith(
        "**Deprecated.** Use add_v2 instead."
    )
    assert "Add two numbers." in operation["description"]


def test_deprecated_directive_marks_a_background_endpoint_deprecated_too(monkeypatch):
    """The identical directive applied to a background/task_id endpoint
    (a function whose name matches LONG_RUNNING_KEYWORDS) -- deprecation
    is orthogonal to sync-vs-background classification, and both branches
    of generate_fastapi_code's own decorator construction must honor it.
    """

    functions = [{"name": "train_model", "args": [], "return_type": "str"}]

    code = generate_fastapi_code(
        functions, deprecated_overrides={"train_model": None}
    )

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    operation = schema["paths"]["/train_model"]["post"]

    assert operation["deprecated"] is True
    assert operation["description"].startswith("**Deprecated.**")


def test_deprecated_overrides_with_no_entry_for_a_function_is_not_deprecated(
    monkeypatch,
):

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(
        functions, deprecated_overrides={"unrelated_function": "reason"}
    )

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    operation = schema["paths"]["/add"]["post"]

    assert operation.get("deprecated", False) is False
    assert "Deprecated" not in operation["description"]


def test_sync_endpoint_documents_401_429_and_500_in_its_openapi_schema(monkeypatch):
    """Confirmed missing before this feature: a generated endpoint's own
    OpenAPI schema documented only its 200 response and FastAPI's own
    automatic 422 -- 401 (verify_api_key) and 429 (_enforce_rate_limit)
    run ahead of every endpoint's own body via Depends(verify_api_key),
    and a sync endpoint's own body can raise 500 (the notebook function's
    exception, or a non-JSON-serializable return value), but none of
    that was ever documented anywhere a caller (or a codegen tool reading
    openapi.json) could actually see it.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    responses = schema["paths"]["/add"]["post"]["responses"]

    assert set(responses) == {"200", "401", "429", "500", "422"}
    assert responses["401"]["description"] == "Missing or invalid X-API-Key header."
    assert "NOTEBOOK_API_RATE_LIMIT_PER_MINUTE" in responses["429"]["description"]
    assert "'add' raised" in responses["500"]["description"]


def test_sync_endpoint_500_description_folds_in_docstring_raises_section(monkeypatch):
    """Confirmed missing before this feature: a notebook author's own
    "Raises:"-section documentation of which exceptions a function can
    raise (and why) -- extract_functions_from_code's own
    "raises_descriptions", from _parse_docstring_raises_descriptions
    (backend/parser/ast_parser.py) -- was never surfaced anywhere in the
    served schema; every sync endpoint's own 500 response description
    stayed the one fixed, generic sentence regardless.
    """

    functions = [{
        "name": "divide",
        "args": [],
        "return_type": "float",
        "raises_descriptions": {
            "ZeroDivisionError": "If b is zero.",
            "ValueError": "If either argument is not finite.",
        },
    }]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    description = (
        schema["paths"]["/divide"]["post"]["responses"]["500"]["description"]
    )

    assert "'divide' raised" in description
    assert "Documented failure modes:" in description
    assert "ZeroDivisionError: If b is zero." in description
    assert "ValueError: If either argument is not finite." in description


def test_sync_endpoint_500_description_omits_failure_modes_when_undocumented(
    monkeypatch,
):

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    description = (
        schema["paths"]["/add"]["post"]["responses"]["500"]["description"]
    )

    assert description == (
        "'add' raised an exception, or returned a value that isn't "
        "JSON-serializable."
    )
    assert "Documented failure modes" not in description


def test_background_endpoint_documents_400_401_429_and_503_in_its_openapi_schema(
    monkeypatch,
):
    """Same gap as the synchronous case above, plus the two extra
    failure modes only a background endpoint's own body can produce:
    503 (NOTEBOOK_API_MAX_TASKS already at capacity) and 400 (a
    caller-supplied ?callback_url= that isn't http(s)).
    """

    functions = [{"name": "train_model", "args": [], "return_type": "str"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    schema = namespace["app"].openapi()
    responses = schema["paths"]["/train_model"]["post"]["responses"]

    assert set(responses) == {"200", "400", "401", "429", "503", "422"}
    assert "callback_url" in responses["400"]["description"]
    assert "NOTEBOOK_API_MAX_TASKS" in responses["503"]["description"]
    assert responses["401"]["description"] == "Missing or invalid X-API-Key header."


def test_documented_401_response_matches_a_real_unauthenticated_request(monkeypatch):
    """The documented 401 example must actually match what a real,
    unauthenticated request gets back -- not just be a plausible-looking
    but disconnected description.
    """

    functions = [{"name": "add", "args": [], "return_type": "int"}]

    code = generate_fastapi_code(functions)

    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    documented_example = (
        namespace["app"].openapi()["paths"]["/add"]["post"]["responses"]["401"]
        ["content"]["application/json"]["example"]
    )

    response = client.post("/add", json={})

    assert response.status_code == 401
    assert response.json() == documented_example


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
        "task_id, req.data, epochs=req.epochs, callback_url=callback_url)"
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


def test_field_uses_docstring_arg_description_over_the_generic_fallback():
    """Confirmed missing before this feature: extract_functions_from_code
    (backend/parser/ast_parser.py) now attaches each parameter's own
    Google-style "Args:" description, but generate_fastapi_code ignored
    it entirely and always fell back to a generic "Parameter 'x' of type
    T" -- no matter how thoroughly the notebook author had actually
    documented the function.
    """

    functions = [
        {
            "name": "train",
            "args": [
                {
                    "name": "epochs", "type": "int", "has_default": False,
                    "kind": "positional",
                    "description": "Number of training passes.",
                },
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "description='Number of training passes.'" in code
    assert "Parameter 'epochs' of type int" not in code


def test_field_falls_back_to_generic_description_when_undocumented():

    functions = [
        {
            "name": "train",
            "args": [
                {
                    "name": "epochs", "type": "int", "has_default": False,
                    "kind": "positional", "description": None,
                },
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "description=\"Parameter 'epochs' of type int\"" in code


def test_response_uses_docstring_return_description_over_the_generic_fallback():
    """Confirmed missing before this feature: extract_functions_from_code
    (backend/parser/ast_parser.py) now attaches a function's own
    Google-style "Returns:" description as "return_description", but
    generate_fastapi_code ignored it entirely and always used the
    generic "Returns {return_type}" -- no matter how thoroughly the
    notebook author had actually documented what the function returns.
    """

    functions = [
        {
            "name": "get_score",
            "args": [],
            "return_type": "float",
            "return_description": "The normalized score between 0 and 1.",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "The normalized score between 0 and 1." in code
    assert "'Returns float'" not in code


def test_response_falls_back_to_generic_description_when_return_undocumented():

    functions = [
        {
            "name": "get_score",
            "args": [],
            "return_type": "float",
            "return_description": None,
        }
    ]

    code = generate_fastapi_code(functions)

    assert "Returns float" in code


def test_field_does_not_override_an_annotations_own_field_description():
    """Confirmed exploitable before this fix: Pydantic merges an
    Annotated[...] metadata's own FieldInfo with the one assigned as the
    field's default value, and the *assigned* one's description wins on
    conflict -- so this generator's own unconditional
    description=repr(field_description) silently discarded a notebook
    author's own Annotated[int, Field(description=...)] description in
    the actual served OpenAPI schema, with nothing to indicate it had
    been overridden.
    """

    functions = [
        {
            "name": "compute",
            "args": [
                {
                    "name": "x",
                    "type": 'Annotated[int, Field(gt=0, description="must be positive")]',
                    "has_default": False, "kind": "positional",
                    "description": None,
                },
            ],
            "return_type": "int",
        }
    ]

    code = generate_fastapi_code(functions)

    class_body = code.split("class ComputeRequest(BaseModel):")[1].split("\n\n")[0]

    assert "must be positive" in class_body
    assert "Parameter 'x' of type" not in class_body
    # No redundant/overriding Field(...) assignment at all -- the
    # Annotated[...] metadata already carries everything meaningful.
    assert "= Field(" not in class_body


def test_field_with_default_still_assigns_default_when_annotation_has_own_description():
    """The no-assignment shortcut above only applies when there's no
    default to assign -- a default must still be attached via
    Field(default=...), but without an overriding description= alongside
    it.
    """

    functions = [
        {
            "name": "compute",
            "args": [
                {
                    "name": "x",
                    "type": 'Annotated[int, Field(gt=0, description="must be positive")]',
                    "has_default": True, "default": 5, "default_is_literal": True,
                    "kind": "positional", "description": None,
                },
            ],
            "return_type": "int",
        }
    ]

    code = generate_fastapi_code(functions)

    class_body = code.split("class ComputeRequest(BaseModel):")[1].split("\n\n")[0]

    assert "Field(default=5)" in class_body
    # Exactly one "description=" -- the annotation's own, embedded
    # inside Annotated[...]; none added by the outer assigned Field(...).
    assert class_body.count("description=") == 1


def test_field_prefers_annotations_own_description_over_docstring_description():
    """When both a docstring Args: entry and the annotation's own
    Annotated[..., Field(description=...)] document the same parameter,
    the more explicit, closer-to-usage annotation wins -- generating a
    redundant/conflicting outer description would be worse than just
    leaving the one already attached directly to the field's own type.
    """

    functions = [
        {
            "name": "compute",
            "args": [
                {
                    "name": "x",
                    "type": 'Annotated[int, Field(description="from annotation")]',
                    "has_default": False, "kind": "positional",
                    "description": "from docstring",
                },
            ],
            "return_type": "int",
        }
    ]

    code = generate_fastapi_code(functions)

    class_body = code.split("class ComputeRequest(BaseModel):")[1].split("\n\n")[0]

    assert "from docstring" not in class_body
    assert "= Field(" not in class_body


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


def test_non_literal_default_is_embedded_as_a_qualified_expression_not_a_string():
    """Confirmed exploitable before this fix: a default that isn't a
    literal_eval-able literal (e.g. a notebook-defined Enum member like
    `Priority.HIGH`) was repr()'d exactly like a real literal default,
    which silently turned it into the *string* "Priority.HIGH" in the
    generated Pydantic model instead of the actual enum member -- a
    caller omitting that field to take its default then passed the raw
    string straight into the notebook's own function, breaking whatever
    it did with the real enum member (e.g. `.value`).
    """

    functions = [
        {
            "name": "set_priority",
            "args": [
                {
                    "name": "priority",
                    "type": "Priority",
                    "has_default": True,
                    "default_is_literal": False,
                    "default": "Priority.HIGH",
                    "kind": "positional",
                },
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert (
        "priority: notebook_module.Priority = Field("
        "default=notebook_module.Priority.HIGH, "
        in code
    )
    assert "default='Priority.HIGH'" not in code
    assert 'default="Priority.HIGH"' not in code


def test_literal_default_is_still_repr_embedded():
    """A real literal default (the common case) must keep going through
    repr(), not the qualification path above -- e.g. a plain string
    default must stay a quoted string literal, not be treated as a bare
    expression referencing a notebook name.
    """

    functions = [
        {
            "name": "greet",
            "args": [
                {
                    "name": "name",
                    "type": "str",
                    "has_default": True,
                    "default_is_literal": True,
                    "default": "world",
                    "kind": "positional",
                },
            ],
            "return_type": "str",
        }
    ]

    code = generate_fastapi_code(functions)

    assert "default='world'" in code


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


def test_dockerfile_content_is_a_pure_string_with_no_disk_access(tmp_path, monkeypatch):
    from backend.generator.docker_generator import dockerfile_content

    # Would fail loudly if dockerfile_content tried to open/write anything --
    # there's no writable cwd for it to do that in.
    monkeypatch.chdir(tmp_path)

    content = dockerfile_content(package_name="my_app", python_version="3.12")

    assert content.startswith("FROM python:3.12-slim")
    assert "COPY . my_app/" in content
    assert 'CMD ["sh", "-c", "uvicorn my_app.app:app' in content
    assert list(tmp_path.iterdir()) == []


def test_dockerfile_content_defaults_match_generate_dockerfiles_own_defaults():
    from backend.generator.docker_generator import dockerfile_content

    content = dockerfile_content()

    assert content.startswith("FROM python:3.11-slim")
    assert "COPY . generated/" in content


def test_generate_dockerfile_writes_exactly_what_dockerfile_content_returns(tmp_path):
    from backend.generator.docker_generator import dockerfile_content, generate_dockerfile

    output_path = tmp_path / "Dockerfile"

    generate_dockerfile(str(output_path), package_name="my_app", python_version="3.13")

    assert output_path.read_text(encoding="utf-8") == dockerfile_content("my_app", "3.13")


def test_dockerignore_content_is_a_pure_string_with_no_disk_access(tmp_path, monkeypatch):
    from backend.generator.docker_generator import dockerignore_content

    monkeypatch.chdir(tmp_path)

    content = dockerignore_content()

    assert ".git/" in content
    assert ".compile_metadata.json" in content
    assert list(tmp_path.iterdir()) == []


def test_generate_dockerignore_writes_exactly_what_dockerignore_content_returns(tmp_path):
    from backend.generator.docker_generator import dockerignore_content, generate_dockerignore

    output_path = tmp_path / ".dockerignore"

    generate_dockerignore(str(output_path))

    assert output_path.read_text(encoding="utf-8") == dockerignore_content()


def test_dockerignore_content_excludes_a_real_env_file(tmp_path, monkeypatch):
    """Confirmed exploitable before this fix: docker_compose_content's own
    docstring already tells an operator to override a NOTEBOOK_API_*
    default for a real deployment via "a `.env` file alongside this
    one" -- the same file env_example_content's own "cp .env.example
    .env" workflow produces -- but only the *template* ".env.example"
    was ever excluded here, never the real ".env" that workflow creates.
    An operator who followed that documented workflow and then ran
    `docker build .` from this directory had `COPY . {package_name}/`
    (dockerfile_content) bake a real NOTEBOOK_API_KEY/
    NOTEBOOK_API_WEBHOOK_SECRET straight into an image layer -- exactly
    the class of build-context leak this .dockerignore already exists to
    prevent for every other file it lists.
    """
    from backend.generator.docker_generator import dockerignore_content

    monkeypatch.chdir(tmp_path)

    content = dockerignore_content()

    assert ".env.example" in content
    assert "\n.env\n" in content
    assert ".env.local" in content
    assert ".env.*.local" in content


def test_dockerignore_env_pattern_actually_matches_a_real_env_filename():
    """Not just a substring check: ".env" must appear as its own pattern
    line, matching a real Docker .dockerignore's line-based glob syntax
    (https://docs.docker.com/engine/reference/builder/#dockerignore-file)
    -- a bare "env" or ".env" glued onto another pattern (e.g. inside
    "*.env.example") would satisfy a naive substring check while still
    never actually excluding a real ".env" file from the build context.
    """
    from backend.generator.docker_generator import dockerignore_content
    from fnmatch import fnmatch

    patterns = [
        line for line in dockerignore_content().splitlines() if line.strip()
    ]

    assert any(fnmatch(".env", pattern) for pattern in patterns)
    assert any(fnmatch(".env.local", pattern) for pattern in patterns)
    assert any(fnmatch(".env.production.local", pattern) for pattern in patterns)
    # .env.example itself must still be excluded independently -- this
    # feature must not have accidentally merged the two patterns.
    assert any(fnmatch(".env.example", pattern) for pattern in patterns)


def test_readme_content_is_a_pure_string_with_no_disk_access(tmp_path, monkeypatch):
    from backend.generator.docker_generator import readme_content

    monkeypatch.chdir(tmp_path)

    content = readme_content(
        package_name="my_app",
        functions=[{"name": "add", "args": [], "return_type": "int"}],
    )

    assert content.startswith("# my_app")
    assert "`POST /add`" in content
    assert list(tmp_path.iterdir()) == []


def test_readme_content_marks_a_background_function_as_such():
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "train_model", "args": [], "return_type": "dict"}],
    )

    assert (
        "`POST /train_model` -- enqueues a background task; poll "
        "`GET /tasks/{task_id}` for the result, or pass `?callback_url=` "
        "to have it POSTed there instead once the task finishes"
    ) in content


def test_readme_content_honors_a_sync_override_for_a_long_running_keyword_match():
    """"regenerate_token" contains "generate" -- without the override,
    this README would wrongly claim it's a background endpoint the same
    way a real compile (absent this fix) would wrongly generate one.
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "regenerate_token", "args": [], "return_type": "str"}],
        background_overrides={"regenerate_token": False},
    )

    assert "`POST /regenerate_token`" in content
    assert "background task" not in content.lower().split(
        "/regenerate_token"
    )[1].split("\n")[0]


def test_readme_content_honors_a_background_override_for_a_non_matching_name():

    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "run_batch_inference", "args": [], "return_type": "int"}],
        background_overrides={"run_batch_inference": True},
    )

    assert "background task" in content.lower().split(
        "/run_batch_inference"
    )[1].split("\n")[0]


def test_readme_content_background_function_mentions_webhook_signing_and_redelivery():
    """Confirmed missing before this fix: a compiled app has supported
    ?callback_url= webhook delivery (with HMAC signing and manual
    redelivery via POST /tasks/{task_id}/redeliver-webhook) for many
    commits, but the one file this tool ships specifically to explain a
    compiled app's own endpoints to a human never mentioned either
    capability at all -- an operator reading only this file had no way to
    learn either exists short of reading api_generator.py's own source.
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "train_model", "args": [], "return_type": "dict"}],
    )

    assert "NOTEBOOK_API_WEBHOOK_SECRET" in content
    assert "/tasks/{task_id}/redeliver-webhook" in content


def test_readme_content_lists_the_redeliver_webhook_route_as_requiring_an_api_key():
    """The explicit `/tasks/...` route list under "So do these built-in
    ones" must name every real /tasks/... route this app actually has --
    redeliver_task_webhook (api_generator.py) included, added several
    commits after this list was first written and never picked up here,
    the identical "claims a status a real request wouldn't get" class of
    documentation bug this function's own docstring already names for the
    original health/ready/... list.
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content()

    assert "`/tasks/{task_id}/redeliver-webhook`" in content


def test_readme_content_background_function_mentions_retry():
    """Confirmed missing before this fix, the identical "server-side
    capability exists, this generator was never updated to match" class
    of documentation bug this function's own docstring already names
    twice: a compiled app has supported POST /tasks/{task_id}/retry
    (actually re-running the underlying function, not just resending an
    already-recorded outcome) for several commits, but the background
    function bullet here still only ever mentioned redeliver-webhook.
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "train_model", "args": [], "return_type": "dict"}],
    )

    assert "/tasks/{task_id}/retry" in content


def test_readme_content_lists_the_retry_route_as_requiring_an_api_key():
    """Mirrors
    test_readme_content_lists_the_redeliver_webhook_route_as_requiring_an_api_key
    for retry_task (api_generator.py) -- the explicit `/tasks/...` route
    list under "So do these built-in ones" must name it too.
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content()

    assert "`/tasks/{task_id}/retry`" in content


def test_readme_content_lists_the_root_route_as_not_requiring_an_api_key():
    """The explicit "does NOT require an X-API-Key" list -- built
    specifically by this function's own first documentation-bug fix to
    be the authoritative, split-out answer to exactly this question --
    never named `GET /` at all, even though it's generated
    (api_generator.py, "# Public infrastructure endpoints") as a plain
    `def root():` with no Depends(verify_api_key), the identical
    unauthenticated shape as every other route already in this list, and
    "root" is itself grouped alongside "health_check"/"readiness_check"/
    "auth_status"/"auth_info" in RESERVED_INFRASTRUCTURE_NAMES
    (api_generator.py). Checked as the precise substring adjacent to
    "/health" in that specific list, not a bare "/" anywhere in the
    content -- a single "/" character would otherwise trivially match
    inside any of the many other route paths this file already mentions
    (e.g. "/tasks/{task_id}").
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content()

    assert "`/`, `/health`, `/ready`, `/info`" in content


def test_readme_content_does_not_mark_a_synchronous_function_as_background():
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "add", "args": [], "return_type": "int"}],
    )

    assert "`POST /add`" in content
    assert "`POST /add` --" not in content


def test_readme_content_marks_a_deprecated_function_with_its_reason():
    """Confirmed missing before this feature: a "# notebook-to-api:
    deprecated" directive already reaches the real compiled endpoint's
    own OpenAPI "deprecated": true, a runtime warning from either
    generated SDK client, and generate_curl_commands/generate_postman_
    collection's own preview output -- but the README a real compile
    also writes said nothing about it at all.
    """
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[
            {"name": "add", "args": [], "return_type": "int"},
            {"name": "old_add", "args": [], "return_type": "int"},
        ],
        deprecated_overrides={"old_add": "use add_v2 instead"},
    )

    assert "`POST /add`\n" in content or content.rstrip().endswith("`POST /add`")
    assert (
        "`POST /old_add` -- **Deprecated.** use add_v2 instead" in content
    )


def test_readme_content_deprecated_with_no_reason_omits_the_trailing_text():
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "old_add", "args": [], "return_type": "int"}],
        deprecated_overrides={"old_add": None},
    )

    assert "`POST /old_add` -- **Deprecated.**" in content
    assert "use" not in content


def test_readme_content_background_and_deprecated_function_gets_both_markers():

    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "train_model", "args": [], "return_type": "str"}],
        deprecated_overrides={"train_model": "use train_v2 instead"},
    )

    assert "enqueues a background task" in content
    assert "**Deprecated.** use train_v2 instead" in content


def test_readme_content_with_no_functions_says_so():
    from backend.generator.docker_generator import readme_content

    content = readme_content(functions=[])

    assert "doesn't expose any functions yet" in content


def test_readme_content_lists_every_env_var_with_its_own_default():
    from backend.generator.docker_generator import readme_content

    env_vars = [
        {
            "name": "NOTEBOOK_API_KEY",
            "default": "notebook-to-api-dev-key",
            "description": "API key(s) accepted on X-API-Key.",
        },
    ]

    content = readme_content(env_vars=env_vars)

    assert (
        "`NOTEBOOK_API_KEY` (default: `notebook-to-api-dev-key`) -- "
        "API key(s) accepted on X-API-Key."
    ) in content


def test_readme_content_defaults_match_generate_readmes_own_defaults():
    from backend.generator.docker_generator import readme_content

    content = readme_content()

    assert content.startswith("# generated")


def test_generate_readme_writes_exactly_what_readme_content_returns(tmp_path):
    from backend.generator.docker_generator import readme_content, generate_readme

    output_path = tmp_path / "README.md"
    functions = [{"name": "add", "args": [], "return_type": "int"}]
    env_vars = [
        {"name": "NOTEBOOK_API_KEY", "default": "dev-key", "description": "d"}
    ]

    generate_readme(str(output_path), "my_app", functions, env_vars)

    assert (
        output_path.read_text(encoding="utf-8")
        == readme_content("my_app", functions, env_vars)
    )

def _deprecation_test_client(monkeypatch, deprecated_overrides,
                             reject_deprecated=None, enforce_sunset=None):
    if enforce_sunset is None:
        monkeypatch.delenv("NOTEBOOK_API_ENFORCE_SUNSET", raising=False)
    else:
        monkeypatch.setenv("NOTEBOOK_API_ENFORCE_SUNSET", enforce_sunset)
    if reject_deprecated is None:
        monkeypatch.delenv("NOTEBOOK_API_REJECT_DEPRECATED", raising=False)
    else:
        monkeypatch.setenv("NOTEBOOK_API_REJECT_DEPRECATED", reject_deprecated)
    functions = [
        {"name": "old_add", "args": [], "return_type": "int"},
        {"name": "add", "args": [], "return_type": "int"},
    ]
    code = generate_fastapi_code(
        functions, deprecated_overrides=deprecated_overrides
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    notebook_module.old_add = lambda: 1
    notebook_module.add = lambda: 2
    monkeypatch.setenv("NOTEBOOK_API_KEY", "test-key")
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    return TestClient(namespace["app"], headers={"X-API-Key": "test-key"})


def test_deprecated_endpoint_response_carries_deprecation_headers(monkeypatch):
    """Confirmed missing before this feature: a deprecated directive only
    reached openapi.json -- a direct caller got no runtime signal at all.
    """
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "Use add instead."}
    )

    response = client.post("/old_add", json={})

    assert response.status_code == 200
    assert response.json() == {"result": 1}
    assert response.headers["Deprecation"] == "true"
    assert response.headers["X-Deprecation-Reason"] == "Use add instead."


def test_non_deprecated_endpoint_response_has_no_deprecation_headers(
    monkeypatch,
):
    client = _deprecation_test_client(monkeypatch, {"old_add": "reason"})

    response = client.post("/add", json={})

    assert response.status_code == 200
    assert "Deprecation" not in response.headers
    assert "X-Deprecation-Reason" not in response.headers


def test_deprecated_endpoint_without_reason_sends_only_deprecation_header(
    monkeypatch,
):
    client = _deprecation_test_client(monkeypatch, {"old_add": None})

    response = client.post("/old_add", json={})

    assert response.headers["Deprecation"] == "true"
    assert "X-Deprecation-Reason" not in response.headers


def test_deprecation_headers_are_sent_on_error_responses_too(monkeypatch):
    """A 422 (bad body) from a deprecated endpoint still tells the caller
    the endpoint is deprecated -- the middleware wraps every response."""
    client = _deprecation_test_client(monkeypatch, {"old_add": None})

    response = client.post("/old_add", content=b"not json",
                           headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    assert response.headers["Deprecation"] == "true"


def test_deprecation_reason_with_quotes_and_newlines_is_sanitized(monkeypatch):
    """A reason containing a quote, a CR/LF and non-ASCII text must neither
    break the generated source nor inject a second header line."""
    client = _deprecation_test_client(
        monkeypatch,
        {"old_add": 'Use "add"\r\nX-Injected: yes \u2014 caf\u00e9'},
    )

    response = client.post("/old_add", json={})

    assert "X-Injected" not in response.headers
    assert response.headers["X-Deprecation-Reason"] == (
        'Use "add" X-Injected: yes caf'
    )


def test_deprecation_header_value_helper_edge_cases():
    from backend.generator.api_generator import _deprecation_header_value

    assert _deprecation_header_value(None) is None
    assert _deprecation_header_value("") is None
    assert _deprecation_header_value("\u2014\n\t") is None
    assert _deprecation_header_value("a" * 500) == "a" * 200
    assert _deprecation_header_value("  spaced   out  ") == "spaced out"


def test_metrics_counts_calls_to_each_deprecated_endpoint(monkeypatch):
    """Confirmed missing before this feature: nothing told an operator
    whether a deprecated endpoint was still being called -- the one signal
    that decides when it's safe to remove."""
    client = _deprecation_test_client(monkeypatch, {"old_add": "Use add."})

    assert client.get("/metrics").json()["deprecated_endpoint_calls"] == {
        "/old_add": 0
    }

    client.post("/old_add", json={})
    client.post("/old_add", json={})
    client.post("/add", json={})

    assert client.get("/metrics").json()["deprecated_endpoint_calls"] == {
        "/old_add": 2
    }

    text = client.get("/metrics/prometheus").text
    assert "# TYPE notebook_api_deprecated_endpoint_calls_total counter" in text
    assert 'notebook_api_deprecated_endpoint_calls_total{path="/old_add"} 2' in text
    assert 'path="/add"' not in text


def test_metrics_deprecated_endpoint_calls_empty_when_nothing_deprecated(
    monkeypatch,
):
    client = _deprecation_test_client(monkeypatch, None)

    client.post("/add", json={})

    assert client.get("/metrics").json()["deprecated_endpoint_calls"] == {}
    assert "deprecated_endpoint_calls_total" not in client.get(
        "/metrics/prometheus"
    ).text


def test_deprecated_endpoint_call_counter_names_are_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_DEPRECATED_ENDPOINTS" in RESERVED_INFRASTRUCTURE_NAMES
    assert "_DEPRECATED_ENDPOINT_CALLS" in RESERVED_INFRASTRUCTURE_NAMES


def test_reject_deprecated_answers_410_without_running_the_function(monkeypatch):
    """Confirmed missing before this feature: there was no way to trial an
    endpoint's removal (a "brownout") short of deleting it and recompiling."""
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "Use add."}, reject_deprecated="true"
    )

    response = client.post("/old_add", json={})

    assert response.status_code == 410
    assert "deprecated" in response.json()["detail"]
    assert response.headers["Deprecation"] == "true"
    assert response.headers["X-Deprecation-Reason"] == "Use add."
    assert client.get("/metrics").json()["deprecated_endpoint_calls"] == {
        "/old_add": 1
    }


def test_reject_deprecated_never_affects_non_deprecated_endpoints(monkeypatch):
    client = _deprecation_test_client(
        monkeypatch, {"old_add": None}, reject_deprecated="true"
    )

    response = client.post("/add", json={})

    assert response.status_code == 200
    assert response.json() == {"result": 2}


def test_reject_deprecated_off_by_default_and_when_false(monkeypatch):
    for value in (None, "false"):
        client = _deprecation_test_client(
            monkeypatch, {"old_add": None}, reject_deprecated=value
        )
        response = client.post("/old_add", json={})
        assert response.status_code == 200
        assert response.json() == {"result": 1}


def test_reject_deprecated_rejects_before_auth_is_checked(monkeypatch):
    client = _deprecation_test_client(
        monkeypatch, {"old_add": None}, reject_deprecated="1"
    )

    response = client.post("/old_add", json={}, headers={"X-API-Key": "wrong"})

    assert response.status_code == 410


def test_get_deprecations_lists_each_deprecated_endpoint_with_reason_and_calls(
    monkeypatch,
):
    """Confirmed missing before this feature: the only way to learn what a
    deployment had deprecated was scanning openapi.json (which
    NOTEBOOK_API_DISABLE_DOCS can hide), and nothing reported whether the
    brownout switch was on."""
    client = _deprecation_test_client(monkeypatch, {"old_add": "Use add."})
    client.post("/old_add", json={})

    response = client.get("/deprecations", headers={"X-API-Key": ""})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.pop("counting_since"), str)
    assert body == {
        "rejecting": False,
        "enforcing_sunset": False,
        "endpoints": [{"path": "/old_add", "reason": "Use add.", "calls": 1, "rejections": 0, "callers": {"testclient": 1}, "sunset": None, "rejected": False, "retired": False}],
    }


def test_get_deprecations_reports_rejecting_and_reason_none(monkeypatch):
    client = _deprecation_test_client(
        monkeypatch, {"old_add": None}, reject_deprecated="true"
    )

    assert _without_counting_since(client.get("/deprecations").json()) == {
        "rejecting": True,
        "enforcing_sunset": False,
        "endpoints": [{"path": "/old_add", "reason": None, "calls": 0, "rejections": 0, "callers": {}, "sunset": None, "rejected": True, "retired": False}],
    }


def test_get_deprecations_is_empty_when_nothing_is_deprecated(monkeypatch):
    client = _deprecation_test_client(monkeypatch, None)

    assert _without_counting_since(client.get("/deprecations").json()) == {
        "rejecting": False, "enforcing_sunset": False, "endpoints": [],
    }


def test_function_named_deprecations_is_reserved():
    functions = [{"name": "deprecations", "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match="deprecations"):
        generate_fastapi_code(functions)


def test_sunset_date_in_reason_emits_rfc8594_sunset_header(monkeypatch):
    """Confirmed missing before this feature: a removal date could only be
    written as prose in the reason -- no Sunset header, nothing in GET
    /deprecations a client or gateway could act on."""
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "Use add. Sunset: 2025-12-31"}
    )

    response = client.post("/old_add", json={})

    assert response.headers["Sunset"] == "Wed, 31 Dec 2025 00:00:00 GMT"
    assert client.get("/deprecations").json()["endpoints"][0]["sunset"] == (
        "2025-12-31"
    )


def test_no_sunset_header_without_a_sunset_marker(monkeypatch):
    client = _deprecation_test_client(monkeypatch, {"old_add": "Use add."})

    response = client.post("/old_add", json={})

    assert "Sunset" not in response.headers
    assert "Sunset" not in client.post("/add", json={}).headers


def test_sunset_header_sent_on_brownout_410_too(monkeypatch):
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "sunset=2026-01-15"}, reject_deprecated="true"
    )

    response = client.post("/old_add", json={})

    assert response.status_code == 410
    assert response.headers["Sunset"] == "Thu, 15 Jan 2026 00:00:00 GMT"


def test_deprecation_sunset_date_helper_edge_cases():
    from backend.generator.api_generator import _deprecation_sunset_date

    assert _deprecation_sunset_date(None) is None
    assert _deprecation_sunset_date("no date here") is None
    assert _deprecation_sunset_date("sunset: 2025-13-40") is None
    assert _deprecation_sunset_date("removed 2025-12-31") is None
    assert _deprecation_sunset_date("SUNSET = 2025-02-28 please") == "2025-02-28"


def test_readme_content_documents_deprecation_runtime_behavior_when_deprecated():
    """Confirmed missing before this feature: the README only marked a
    deprecated endpoint's bullet -- nothing told an operator about the
    Deprecation/Sunset headers, GET /deprecations, the
    NOTEBOOK_API_REJECT_DEPRECATED brownout, or the CLI removal gates."""
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "add"}, {"name": "old_add"}, {"name": "older"}],
        deprecated_overrides={
            "old_add": "Use add. sunset: 2025-12-31",
            "older": None,
        },
    )

    assert "## Deprecations" in content
    assert "2 endpoint(s) above are deprecated." in content
    assert "`POST /old_add` -- removal scheduled for 2025-12-31" in content
    assert "`POST /older` -- removal scheduled" not in content
    assert "`GET /deprecations`" in content
    assert "NOTEBOOK_API_REJECT_DEPRECATED=true" in content
    assert "--fail-if-called" in content and "--fail-if-past-sunset" in content


def test_readme_content_omits_deprecations_section_when_nothing_deprecated():
    from backend.generator.docker_generator import readme_content

    content = readme_content(functions=[{"name": "add"}])

    assert "## Deprecations" not in content
    assert "Scheduled removals" not in content
    # The endpoint itself exists on every compiled app, so it's always
    # listed among the unauthenticated built-ins.
    assert "`/deprecations`, `/auth/status`" in content
    assert "- `POST /add`\n\nInteractive docs" in content


def test_readme_content_no_scheduled_removals_without_a_valid_sunset():
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "old_add"}],
        deprecated_overrides={"old_add": "sunset: 2025-13-40"},
    )

    assert "## Deprecations" in content
    assert "Scheduled removals" not in content


def test_openapi_operation_carries_sunset_extension_for_a_dated_deprecation(
    monkeypatch,
):
    """Confirmed missing before this feature: a directive's sunset date
    only ever reached the Sunset response header -- nothing reading
    openapi.json (an SDK generator, a linter, a gateway) could see it."""
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "Use add. sunset: 2025-12-31"}
    )

    schema = client.get("/openapi.json").json()

    assert schema["paths"]["/old_add"]["post"]["x-notebook-to-api-sunset"] == (
        "2025-12-31"
    )
    assert "x-notebook-to-api-sunset" not in schema["paths"]["/add"]["post"]


def test_openapi_operation_has_no_sunset_extension_without_a_valid_date(
    monkeypatch,
):
    client = _deprecation_test_client(monkeypatch, {"old_add": "sunset: 2025-13-40"})

    operation = client.get("/openapi.json").json()["paths"]["/old_add"]["post"]

    assert operation["deprecated"] is True
    assert "x-notebook-to-api-sunset" not in operation


def test_background_endpoint_openapi_operation_carries_sunset_extension_too(
    monkeypatch,
):
    code = generate_fastapi_code(
        [{"name": "train_model", "args": [], "return_type": "str"}],
        deprecated_overrides={"train_model": "sunset=2026-01-15"},
    )
    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    operation = namespace["app"].openapi()["paths"]["/train_model"]["post"]

    assert operation["x-notebook-to-api-sunset"] == "2026-01-15"


def test_enforce_sunset_rejects_a_deprecated_endpoint_past_its_sunset(monkeypatch):
    """Confirmed missing before this feature: a Sunset date was only ever
    advisory -- the endpoint kept serving after it unless someone flipped
    NOTEBOOK_API_REJECT_DEPRECATED (or recompiled) on the day."""
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "sunset: 2000-01-01"}, enforce_sunset="true"
    )

    response = client.post("/old_add", json={})

    assert response.status_code == 410
    assert response.headers["Sunset"] == "Sat, 01 Jan 2000 00:00:00 GMT"
    assert client.post("/add", json={}).status_code == 200


def test_enforce_sunset_still_serves_before_the_sunset_date(monkeypatch):
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "sunset: 2999-01-01"}, enforce_sunset="true"
    )

    assert client.post("/old_add", json={}).status_code == 200


def test_enforce_sunset_ignores_deprecations_without_a_sunset(monkeypatch):
    client = _deprecation_test_client(
        monkeypatch, {"old_add": "Use add."}, enforce_sunset="true"
    )

    assert client.post("/old_add", json={}).status_code == 200


def test_past_sunset_is_still_served_when_enforce_sunset_is_off(monkeypatch):
    for value in (None, "false"):
        client = _deprecation_test_client(
            monkeypatch, {"old_add": "sunset: 2000-01-01"}, enforce_sunset=value
        )
        response = client.post("/old_add", json={})
        assert response.status_code == 200
        assert response.headers["Deprecation"] == "true"


def test_enforce_sunset_backing_names_are_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "ENFORCE_DEPRECATION_SUNSET" in RESERVED_INFRASTRUCTURE_NAMES
    assert "_sunset_has_passed" in RESERVED_INFRASTRUCTURE_NAMES


def test_get_deprecations_reports_per_endpoint_rejected_under_enforce_sunset(
    monkeypatch,
):
    """Confirmed missing before this feature: GET /deprecations only had
    the global "rejecting" (the brownout switch) -- with
    NOTEBOOK_API_ENFORCE_SUNSET rejecting individual endpoints by date,
    nothing said which ones were actually answering 410 right now."""
    client = _deprecation_test_client(
        monkeypatch,
        {"old_add": "sunset: 2000-01-01", "add": "sunset: 2999-01-01"},
        enforce_sunset="true",
    )

    body = client.get("/deprecations").json()

    assert body["rejecting"] is False
    assert body["enforcing_sunset"] is True
    by_path = {entry["path"]: entry for entry in body["endpoints"]}
    assert by_path["/old_add"]["rejected"] is True
    assert by_path["/add"]["rejected"] is False
    # "rejected" must agree with what the endpoint actually does.
    assert client.post("/old_add", json={}).status_code == 410
    assert client.post("/add", json={}).status_code == 200


def test_metrics_count_rejected_deprecated_calls_separately(monkeypatch):
    """Confirmed missing before this feature: /metrics counted every call
    to a deprecated endpoint the same whether it was served or answered
    410 -- no way to see how many callers a brownout actually broke."""
    client = _deprecation_test_client(
        monkeypatch,
        {"old_add": "sunset: 2000-01-01", "add": "sunset: 2999-01-01"},
        enforce_sunset="true",
    )

    client.post("/old_add", json={})
    client.post("/old_add", json={})
    client.post("/add", json={})

    metrics = client.get("/metrics").json()
    assert metrics["deprecated_endpoint_calls"] == {"/old_add": 2, "/add": 1}
    assert metrics["deprecated_endpoint_rejections"] == {"/old_add": 2, "/add": 0}

    text = client.get("/metrics/prometheus").text
    assert "# TYPE notebook_api_deprecated_endpoint_rejections_total counter" in text
    assert 'notebook_api_deprecated_endpoint_rejections_total{path="/old_add"} 2' in text
    assert 'notebook_api_deprecated_endpoint_rejections_total{path="/add"} 0' in text


def test_metrics_rejections_stay_zero_when_nothing_is_rejected(monkeypatch):
    client = _deprecation_test_client(monkeypatch, {"old_add": None})

    client.post("/old_add", json={})

    metrics = client.get("/metrics").json()
    assert metrics["deprecated_endpoint_calls"] == {"/old_add": 1}
    assert metrics["deprecated_endpoint_rejections"] == {"/old_add": 0}


def test_deprecated_endpoint_rejections_counter_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_DEPRECATED_ENDPOINT_REJECTIONS" in RESERVED_INFRASTRUCTURE_NAMES


def test_get_deprecations_reports_each_endpoints_rejection_count(monkeypatch):
    """Confirmed missing before this feature: GET /deprecations said
    whether an endpoint is rejecting now, but not how many calls it has
    already turned away -- only /metrics carried that count."""
    client = _deprecation_test_client(
        monkeypatch,
        {"old_add": "sunset: 2000-01-01", "add": "sunset: 2999-01-01"},
        enforce_sunset="true",
    )
    client.post("/old_add", json={})
    client.post("/old_add", json={})
    client.post("/add", json={})

    by_path = {
        entry["path"]: entry for entry in client.get("/deprecations").json()["endpoints"]
    }

    assert (by_path["/old_add"]["calls"], by_path["/old_add"]["rejections"]) == (2, 2)
    assert (by_path["/add"]["calls"], by_path["/add"]["rejections"]) == (1, 0)
    # Agrees with /metrics' own per-path counter.
    assert client.get("/metrics").json()["deprecated_endpoint_rejections"] == {
        "/old_add": 2, "/add": 0,
    }


def test_json_request_log_identifies_callers_of_a_deprecated_endpoint(
    monkeypatch, capsys
):
    """Confirmed missing before this feature: the call counters said how
    many calls a deprecated endpoint still got, but nothing recorded who
    was making them -- the one thing needed to get them to migrate."""
    monkeypatch.setenv("NOTEBOOK_API_JSON_LOGS", "true")
    client = _deprecation_test_client(monkeypatch, {"old_add": "Use add."})
    capsys.readouterr()

    client.post("/old_add", json={}, headers={"User-Agent": "billing-cron/2.1"})
    client.post("/add", json={})

    entries = [
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    by_path = {entry["path"]: entry for entry in entries}
    assert by_path["/old_add"]["deprecated"] is True
    assert by_path["/old_add"]["user_agent"] == "billing-cron/2.1"
    assert by_path["/old_add"]["client_ip"] == "testclient"
    assert by_path["/add"]["deprecated"] is False
    assert "client_ip" not in by_path["/add"]


def test_json_request_log_marks_a_rejected_deprecated_call_too(monkeypatch, capsys):
    monkeypatch.setenv("NOTEBOOK_API_JSON_LOGS", "true")
    client = _deprecation_test_client(
        monkeypatch, {"old_add": None}, reject_deprecated="true"
    )
    capsys.readouterr()

    client.post("/old_add", json={}, headers={"User-Agent": "legacy-app"})

    entry = next(
        json.loads(line) for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    )
    assert entry["status_code"] == 410
    assert entry["deprecated"] is True
    assert entry["user_agent"] == "legacy-app"


def test_get_deprecations_breaks_calls_down_by_user_agent(monkeypatch):
    """Confirmed missing before this feature: GET /deprecations said how
    many calls a deprecated endpoint got, never from whom -- identifying a
    caller required a log pipeline over NOTEBOOK_API_JSON_LOGS."""
    client = _deprecation_test_client(monkeypatch, {"old_add": None})
    for agent in ("billing-cron/2", "billing-cron/2", "mobile/1"):
        client.post("/old_add", json={}, headers={"User-Agent": agent})
    client.post("/old_add", json={}, headers={"User-Agent": ""})

    entry = client.get("/deprecations").json()["endpoints"][0]

    assert entry["callers"] == {"billing-cron/2": 2, "(none)": 1, "mobile/1": 1}
    assert list(entry["callers"]) == ["billing-cron/2", "(none)", "mobile/1"]


def test_deprecated_callers_are_bounded_per_path(monkeypatch):
    client = _deprecation_test_client(monkeypatch, {"old_add": None})
    for i in range(55):
        client.post("/old_add", json={}, headers={"User-Agent": f"agent-{i}" + "x" * 300})

    callers = client.get("/deprecations").json()["endpoints"][0]["callers"]

    assert len(callers) == 51  # 50 distinct + "(other)"
    assert callers["(other)"] == 5
    assert all(len(agent) <= 200 for agent in callers)


def test_post_deprecations_reset_zeroes_every_deprecation_counter(monkeypatch):
    """Confirmed missing before this feature: the deprecation counters only
    ever grew for the process's lifetime -- no fresh measurement after
    contacting callers without restarting the app."""
    client = _deprecation_test_client(
        monkeypatch, {"old_add": None}, reject_deprecated="true"
    )
    client.post("/old_add", json={}, headers={"User-Agent": "cron"})

    response = client.post("/deprecations/reset")

    assert response.status_code == 200
    assert _without_counting_since(response.json()) == {"reset": ["/old_add"]}
    entry = client.get("/deprecations").json()["endpoints"][0]
    assert (entry["calls"], entry["rejections"], entry["callers"]) == (0, 0, {})
    metrics = client.get("/metrics").json()
    assert metrics["deprecated_endpoint_calls"] == {"/old_add": 0}
    assert metrics["deprecated_endpoint_rejections"] == {"/old_add": 0}

    client.post("/old_add", json={}, headers={"User-Agent": "cron"})
    assert client.get("/deprecations").json()["endpoints"][0]["calls"] == 1


def test_post_deprecations_reset_requires_the_api_key(monkeypatch):
    client = _deprecation_test_client(monkeypatch, {"old_add": None})

    response = client.post("/deprecations/reset", headers={"X-API-Key": "wrong"})

    assert response.status_code == 401


def test_function_named_reset_deprecation_counters_is_reserved():
    functions = [{"name": "reset_deprecation_counters", "args": [], "return_type": "dict"}]

    with pytest.raises(ReservedFunctionNameError, match="reset_deprecation_counters"):
        generate_fastapi_code(functions)


def _without_counting_since(body):
    body = dict(body)
    body.pop("counting_since")
    return body


def test_get_deprecations_reports_when_counting_started_and_reset_moves_it(
    monkeypatch,
):
    """Confirmed missing before this feature: GET /deprecations' counts
    had no time window -- "3 calls" could mean an hour or a month."""
    import re
    import time as time_module

    client = _deprecation_test_client(monkeypatch, {"old_add": None})
    since = client.get("/deprecations").json()["counting_since"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", since)

    time_module.sleep(1.1)
    reset = client.post("/deprecations/reset").json()

    assert reset["counting_since"] > since
    assert client.get("/deprecations").json()["counting_since"] == reset["counting_since"]


def test_deprecation_counters_since_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_DEPRECATION_COUNTERS_SINCE" in RESERVED_INFRASTRUCTURE_NAMES


def test_retired_endpoint_answers_410_with_its_deprecation_headers(monkeypatch):
    """Confirmed missing before this feature: a deprecated function left
    out of a compile past its sunset (`--drop-past-sunset`) simply vanished,
    so a caller still using it got a bare 404 -- indistinguishable from a
    typo'd URL -- instead of 410 Gone."""
    code = generate_fastapi_code(
        [{"name": "add", "args": [], "return_type": "int"}],
        retired_endpoints={"old_add": "Use add. sunset: 2000-01-01"},
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    notebook_module.add = lambda: 1
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"])
    response = client.post("/old_add", json={})

    assert response.status_code == 410
    assert response.json() == {"detail": "'/old_add' has been removed (sunset 2000-01-01)."}
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Sunset"] == "Sat, 01 Jan 2000 00:00:00 GMT"
    assert response.headers["X-Deprecation-Reason"] == "Use add. sunset: 2000-01-01"
    # Hidden from the published schema -- it's not a callable endpoint.
    assert "/old_add" not in namespace["app"].openapi()["paths"]


def test_no_retired_endpoints_means_no_extra_routes(monkeypatch):
    code = generate_fastapi_code([{"name": "add", "args": [], "return_type": "int"}])

    assert "_retired_endpoint_" not in code


def _retired_client(monkeypatch):
    code = generate_fastapi_code(
        [{"name": "add", "args": [], "return_type": "int"}],
        retired_endpoints={"old_add": "Use add. sunset: 2000-01-01"},
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    notebook_module.add = lambda: 1
    monkeypatch.setenv("NOTEBOOK_API_KEY", "test-key")
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    return TestClient(namespace["app"], headers={"X-API-Key": "test-key"})


def test_retired_endpoints_are_tracked_by_get_deprecations(monkeypatch):
    """Confirmed missing before this feature: a retired (410 tombstone)
    endpoint was invisible to GET /deprecations and every counter, so an
    operator couldn't see who kept calling it after its removal."""
    client = _retired_client(monkeypatch)
    for _ in range(2):
        response = client.post("/old_add", json={}, headers={"User-Agent": "legacy/1"})

    # Still the tombstone's own, more specific answer.
    assert response.status_code == 410
    assert "has been removed" in response.json()["detail"]
    assert response.headers["Sunset"] == "Sat, 01 Jan 2000 00:00:00 GMT"

    entry = client.get("/deprecations").json()["endpoints"][0]
    assert entry["path"] == "/old_add"
    assert (entry["retired"], entry["rejected"]) == (True, True)
    assert (entry["calls"], entry["rejections"]) == (2, 2)
    assert entry["callers"] == {"legacy/1": 2}
    assert entry["sunset"] == "2000-01-01"
    assert client.get("/metrics").json()["deprecated_endpoint_rejections"] == {"/old_add": 2}


def test_retired_endpoints_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_RETIRED_ENDPOINTS" in RESERVED_INFRASTRUCTURE_NAMES


def _request_timeout_client(monkeypatch, timeout, impl, is_async=False):
    code = generate_fastapi_code(
        [{"name": "slow", "args": [], "return_type": "int", "is_async": is_async}]
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    notebook_module.slow = impl
    monkeypatch.setenv("NOTEBOOK_API_KEY", "test-key")
    if timeout is None:
        monkeypatch.delenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", str(timeout))
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    return TestClient(namespace["app"], headers={"X-API-Key": "test-key"})


def test_request_timeout_answers_504_for_a_hung_sync_endpoint(monkeypatch):
    """Confirmed missing before this feature: a synchronous endpoint whose
    notebook function hung held its request open forever -- only
    background tasks had NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS."""
    import time as time_module

    client = _request_timeout_client(monkeypatch, 1, lambda: time_module.sleep(3) or 1)

    started = time_module.monotonic()
    response = client.post("/slow", json={})
    elapsed = time_module.monotonic() - started

    assert response.status_code == 504
    assert "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS (1s)" in response.json()["detail"]
    assert elapsed < 2.5


def test_request_timeout_lets_a_fast_call_through(monkeypatch):
    client = _request_timeout_client(monkeypatch, 5, lambda: 7)

    response = client.post("/slow", json={})

    assert response.status_code == 200
    assert response.json() == {"result": 7}


def test_request_timeout_is_off_by_default(monkeypatch):
    import time as time_module

    client = _request_timeout_client(monkeypatch, None, lambda: time_module.sleep(1.2) or 3)

    response = client.post("/slow", json={})

    assert response.status_code == 200
    assert response.json() == {"result": 3}


def test_request_timeout_applies_to_async_notebook_functions_too(monkeypatch):
    import asyncio

    async def slow():
        await asyncio.sleep(3)
        return 1

    client = _request_timeout_client(monkeypatch, 1, slow, is_async=True)

    response = client.post("/slow", json={})

    assert response.status_code == 504


def test_request_timeout_names_are_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "REQUEST_TIMEOUT_SECONDS" in RESERVED_INFRASTRUCTURE_NAMES
    assert "_call_notebook_function" in RESERVED_INFRASTRUCTURE_NAMES


@pytest.mark.parametrize("value, expected", [("30", 30), ("0", None), (None, None)])
def test_generated_app_get_config_reports_request_timeout_seconds(
    monkeypatch, value, expected
):
    """Confirmed missing before this feature: GET /config reported every
    other configured limit but not NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS."""
    client = _request_timeout_client(
        monkeypatch, None if value is None else int(value), lambda: 1
    )

    assert client.get("/config").json()["request_timeout_seconds"] == expected


def test_metrics_count_request_timeouts_per_endpoint(monkeypatch):
    """Confirmed missing before this feature: a 504 from
    NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS only bumped the generic 5xx
    counter -- nothing said which endpoint kept hitting the limit."""
    import time as time_module

    client = _request_timeout_client(monkeypatch, 1, lambda: time_module.sleep(2) or 1)
    assert client.get("/metrics").json()["request_timeouts_by_endpoint"] == {}
    assert "request_timeouts_total" not in client.get("/metrics/prometheus").text

    client.post("/slow", json={})
    client.post("/slow", json={})

    assert client.get("/metrics").json()["request_timeouts_by_endpoint"] == {"/slow": 2}
    text = client.get("/metrics/prometheus").text
    assert "# TYPE notebook_api_request_timeouts_total counter" in text
    assert 'notebook_api_request_timeouts_total{path="/slow"} 2' in text


def test_request_timeouts_counter_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_REQUEST_TIMEOUTS" in RESERVED_INFRASTRUCTURE_NAMES


def _per_endpoint_timeout_client(monkeypatch, global_timeout, overrides, impl):
    code = generate_fastapi_code(
        [{"name": "slow", "args": [], "return_type": "int"}],
        timeout_overrides=overrides,
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    notebook_module.slow = impl
    monkeypatch.setenv("NOTEBOOK_API_KEY", "test-key")
    if global_timeout is None:
        monkeypatch.delenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS", str(global_timeout))
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    return TestClient(namespace["app"], headers={"X-API-Key": "test-key"})


def test_timeout_directive_bounds_one_endpoint_without_a_global_timeout(monkeypatch):
    """Confirmed missing before this feature: NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS
    was the only bound, shared by every synchronous endpoint."""
    import time as time_module

    client = _per_endpoint_timeout_client(
        monkeypatch, None, {"slow": 1}, lambda: time_module.sleep(3) or 1
    )

    response = client.post("/slow", json={})

    assert response.status_code == 504
    assert "its own timeout directive (1s)" in response.json()["detail"]


def test_timeout_directive_zero_exempts_an_endpoint_from_the_global_timeout(monkeypatch):
    import time as time_module

    client = _per_endpoint_timeout_client(
        monkeypatch, 1, {"slow": 0}, lambda: time_module.sleep(1.5) or 9
    )

    response = client.post("/slow", json={})

    assert response.status_code == 200
    assert response.json() == {"result": 9}


def test_timeout_directive_is_published_in_the_openapi_schema(monkeypatch):
    """Confirmed missing before this feature: an endpoint's own timeout
    directive changed its behavior but never reached openapi.json -- no
    extension a client could size its HTTP timeout from, no 504 documented."""
    code = generate_fastapi_code(
        [
            {"name": "slow", "args": [], "return_type": "int"},
            {"name": "exempt", "args": [], "return_type": "int"},
            {"name": "plain", "args": [], "return_type": "int"},
        ],
        timeout_overrides={"slow": 30, "exempt": 0},
    )
    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    paths = namespace["app"].openapi()["paths"]
    slow, exempt, plain = (paths[f"/{n}"]["post"] for n in ("slow", "exempt", "plain"))

    assert slow["x-notebook-to-api-timeout-seconds"] == 30
    assert "30s timeout" in slow["responses"]["504"]["description"]
    assert exempt["x-notebook-to-api-timeout-seconds"] == 0
    assert "504" not in exempt["responses"]
    assert "x-notebook-to-api-timeout-seconds" not in plain
    assert "504" not in plain["responses"]


def test_readme_content_notes_each_endpoints_own_timeout():
    """Confirmed missing before this feature: the README marked background
    and deprecated endpoints but said nothing about an endpoint's own
    timeout directive -- the one behavior a caller must plan around."""
    from backend.generator.docker_generator import readme_content

    content = readme_content(
        functions=[{"name": "report"}, {"name": "exempt"}, {"name": "add"},
                   {"name": "train_model"}],
        timeout_overrides={"report": 30, "exempt": 0, "train_model": 10},
    )

    assert "- `POST /report` -- answers `504` if it runs longer than 30s" in content
    assert "- `POST /exempt` -- exempt from `NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS`" in content
    assert "- `POST /add`\n" in content
    # A background endpoint's directive bounds its task instead.
    assert "- `POST /train_model` -- enqueues a background task" in content
    assert "its task fails if it runs longer than 10s" in content


def test_get_config_reports_each_endpoints_own_timeout(monkeypatch):
    """Confirmed missing before this feature: GET /config reported only the
    global request timeout, never an endpoint's own timeout directive."""
    code = generate_fastapi_code(
        [
            {"name": "report", "args": [], "return_type": "int"},
            {"name": "exempt", "args": [], "return_type": "int"},
            {"name": "add", "args": [], "return_type": "int"},
            {"name": "train_model", "args": [], "return_type": "int"},
        ],
        timeout_overrides={"report": 30, "exempt": 0, "train_model": 10},
    )
    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    config = TestClient(namespace["app"]).get("/config").json()

    # train_model is a background endpoint -- its directive bounds its task.
    assert config["endpoint_timeouts"] == {"/exempt": 0, "/report": 30, "/train_model": 10}


def test_endpoint_timeouts_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_ENDPOINT_TIMEOUTS" in RESERVED_INFRASTRUCTURE_NAMES


def test_request_timeout_504_carries_the_timeout_marker_header(monkeypatch):
    """A 504 from the app's own request timeout is marked, so clients can
    tell it from a transient gateway 504 and not retry it."""
    import time as time_module

    client = _request_timeout_client(monkeypatch, 1, lambda: time_module.sleep(2) or 1)

    response = client.post("/slow", json={})

    assert response.status_code == 504
    assert response.headers["X-Notebook-API-Timeout"] == "true"


def _background_timeout_client(monkeypatch, overrides, impl, global_timeout=None):
    code = generate_fastapi_code(
        [{"name": "train_model", "args": [], "return_type": "int"}],
        timeout_overrides=overrides,
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    impl.__name__ = "train_model"
    notebook_module.train_model = impl
    monkeypatch.setenv("NOTEBOOK_API_KEY", "test-key")
    if global_timeout is None:
        monkeypatch.delenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS", str(global_timeout))
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    return TestClient(namespace["app"], headers={"X-API-Key": "test-key"})


def test_timeout_directive_bounds_a_background_functions_task(monkeypatch):
    """Confirmed missing before this feature: a timeout directive on a
    background function was silently ignored -- only the global
    NOTEBOOK_API_TASK_EXECUTION_TIMEOUT_SECONDS could bound its tasks."""
    import time as time_module

    def slow():
        time_module.sleep(3)
        return 1

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, slow)

    task_id = client.post("/train_model", json={}).json()["task_id"]
    task = client.get(f"/tasks/{task_id}").json()

    assert task["status"] == "failed"
    assert task["error"] == "Task exceeded its 1s execution timeout"


def test_background_timeout_directive_zero_exempts_from_the_global_limit(monkeypatch):
    import time as time_module

    def slow():
        time_module.sleep(1.5)
        return 7

    client = _background_timeout_client(monkeypatch, {"train_model": 0}, slow, global_timeout=1)

    task_id = client.post("/train_model", json={}).json()["task_id"]
    task = client.get(f"/tasks/{task_id}").json()

    assert task["status"] == "completed"
    assert task["result"] == 7


def test_task_timeouts_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_TASK_TIMEOUTS" in RESERVED_INFRASTRUCTURE_NAMES


def test_background_endpoint_publishes_its_task_timeout_in_openapi(monkeypatch):
    """Confirmed missing before this feature: a background endpoint's own
    task timeout directive never reached openapi.json."""
    code = generate_fastapi_code(
        [{"name": "train_model", "args": [], "return_type": "int"},
         {"name": "fit_model", "args": [], "return_type": "int"}],
        timeout_overrides={"train_model": 300},
    )
    _register_fake_notebook_module(monkeypatch)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    paths = namespace["app"].openapi()["paths"]

    assert paths["/train_model"]["post"]["x-notebook-to-api-timeout-seconds"] == 300
    assert "x-notebook-to-api-timeout-seconds" not in paths["/fit_model"]["post"]
    # A background endpoint never answers 504 -- its task fails instead.
    assert "504" not in paths["/train_model"]["post"]["responses"]


def test_metrics_count_background_task_timeouts_per_endpoint(monkeypatch):
    """Confirmed missing before this feature: a background task failing on
    its execution timeout was counted nowhere -- only synchronous request
    timeouts had a per-endpoint counter."""
    import time as time_module

    def slow():
        time_module.sleep(2)
        return 1

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, slow)
    assert client.get("/metrics").json()["task_timeouts_by_endpoint"] == {}
    assert "task_timeouts_total" not in client.get("/metrics/prometheus").text

    client.post("/train_model", json={})

    assert client.get("/metrics").json()["task_timeouts_by_endpoint"] == {"/train_model": 1}
    text = client.get("/metrics/prometheus").text
    assert "# TYPE notebook_api_task_timeouts_total counter" in text
    assert 'notebook_api_task_timeouts_total{path="/train_model"} 1' in text


def test_task_timeout_failures_name_is_reserved():
    from backend.generator.api_generator import RESERVED_INFRASTRUCTURE_NAMES

    assert "_TASK_TIMEOUT_FAILURES" in RESERVED_INFRASTRUCTURE_NAMES


def test_timed_out_background_task_is_marked_timed_out(monkeypatch):
    """Confirmed missing before this feature: a task failed by its own
    execution timeout was indistinguishable from one whose function raised,
    short of parsing its "error" text."""
    import time as time_module

    def slow():
        time_module.sleep(2)
        return 1

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, slow)

    task_id = client.post("/train_model", json={}).json()["task_id"]
    task = client.get(f"/tasks/{task_id}").json()

    assert task["status"] == "failed"
    assert task["timed_out"] is True


def test_a_task_that_raises_is_not_marked_timed_out(monkeypatch):
    def broken():
        raise ValueError("boom")

    client = _background_timeout_client(monkeypatch, {"train_model": 5}, broken)

    task_id = client.post("/train_model", json={}).json()["task_id"]
    task = client.get(f"/tasks/{task_id}").json()

    assert task["status"] == "failed"
    assert "timed_out" not in task


def test_timed_out_task_webhook_payload_says_so(monkeypatch):
    import time as time_module

    def slow():
        time_module.sleep(2)
        return 1

    code = generate_fastapi_code(
        [{"name": "train_model", "args": [], "return_type": "int"}],
        timeout_overrides={"train_model": 1},
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    slow.__name__ = "train_model"
    notebook_module.train_model = slow
    monkeypatch.setenv("NOTEBOOK_API_KEY", "test-key")
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)
    delivered = []
    namespace["_deliver_task_webhook"] = (
        lambda url, payload: delivered.append(payload) or {"delivered": True}
    )

    from fastapi.testclient import TestClient

    client = TestClient(namespace["app"], headers={"X-API-Key": "test-key"})
    response = client.post(
        "/train_model", json={}, params={"callback_url": "https://example.com/hook"}
    )

    assert response.status_code == 200, response.text
    assert len(delivered) == 1
    assert delivered[0]["status"] == "failed"
    assert delivered[0]["timed_out"] is True


def test_list_tasks_filters_by_timed_out(monkeypatch):
    """Confirmed missing before this feature: GET /tasks could filter by
    status and webhook outcome, but not pick out tasks that timed out."""
    import time as time_module

    calls = {"n": 0}

    def sometimes_slow():
        calls["n"] += 1
        if calls["n"] == 1:
            time_module.sleep(2)
        return calls["n"]

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, sometimes_slow)
    timed_out_id = client.post("/train_model", json={}).json()["task_id"]
    ok_id = client.post("/train_model", json={}).json()["task_id"]

    timed_out = client.get("/tasks", params={"timed_out": "true"}).json()
    not_timed_out = client.get("/tasks", params={"timed_out": "false"}).json()
    everything = client.get("/tasks").json()

    ids = lambda body: set(body["tasks"])
    assert ids(timed_out) == {timed_out_id}
    assert ids(not_timed_out) == {ok_id}
    assert ids(everything) == {timed_out_id, ok_id}


def test_retry_refuses_a_timed_out_task_unless_forced(monkeypatch):
    """Confirmed missing before this feature: retrying a task that failed by
    exceeding its execution timeout just re-ran the same slow function (and
    its side effects) for the full limit again, with no warning."""
    import time as time_module

    def slow():
        time_module.sleep(2)
        return 1

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, slow)
    task_id = client.post("/train_model", json={}).json()["task_id"]

    refused = client.post(f"/tasks/{task_id}/retry")
    forced = client.post(f"/tasks/{task_id}/retry", params={"force": "true"})

    assert refused.status_code == 409
    assert "?force=true" in refused.json()["detail"]
    assert forced.status_code == 200, forced.text
    assert forced.json()["task_id"] != task_id


def test_retry_of_an_ordinary_failure_needs_no_force(monkeypatch):
    def broken():
        raise ValueError("boom")

    client = _background_timeout_client(monkeypatch, {"train_model": 5}, broken)
    task_id = client.post("/train_model", json={}).json()["task_id"]

    response = client.post(f"/tasks/{task_id}/retry")

    assert response.status_code == 200, response.text


def test_list_tasks_reports_a_timed_out_task_count(monkeypatch):
    """Confirmed missing before this feature: GET /tasks counted completed,
    failed and webhook-failed tasks, but not timed-out ones."""
    import time as time_module

    calls = {"n": 0}

    def sometimes_slow():
        calls["n"] += 1
        if calls["n"] == 1:
            time_module.sleep(2)
        return calls["n"]

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, sometimes_slow)
    client.post("/train_model", json={})
    client.post("/train_model", json={})

    body = client.get("/tasks").json()
    filtered = client.get("/tasks", params={"status": "completed"}).json()

    assert body["timed_out_tasks"] == 1
    # Counted across every task, regardless of the filter applied.
    assert filtered["timed_out_tasks"] == 1


def test_delete_failed_tasks_can_target_only_timed_out_ones(monkeypatch):
    """Confirmed missing before this feature: DELETE /tasks/failed purged
    every failed task -- no way to clear only the timed-out ones while
    keeping genuine errors around to debug."""
    import time as time_module

    calls = {"n": 0}

    def slow_then_broken():
        calls["n"] += 1
        if calls["n"] == 1:
            time_module.sleep(2)
            return 1
        raise ValueError("boom")

    client = _background_timeout_client(monkeypatch, {"train_model": 1}, slow_then_broken)
    timed_out_id = client.post("/train_model", json={}).json()["task_id"]
    broken_id = client.post("/train_model", json={}).json()["task_id"]

    response = client.delete("/tasks/failed", params={"timed_out": "true"})

    assert response.json()["deleted"] == 1
    remaining = set(client.get("/tasks").json()["tasks"])
    assert remaining == {broken_id}
    assert timed_out_id not in remaining


def test_delete_failed_tasks_without_the_filter_still_purges_all(monkeypatch):
    def broken():
        raise ValueError("boom")

    client = _background_timeout_client(monkeypatch, {"train_model": 5}, broken)
    client.post("/train_model", json={})
    client.post("/train_model", json={})

    assert client.delete("/tasks/failed").json()["deleted"] == 2


def _endpoint_rate_limit_client(monkeypatch, overrides):
    code = generate_fastapi_code(
        [
            {"name": "limited", "args": [], "return_type": "int"},
            {"name": "free", "args": [], "return_type": "int"},
            {"name": "train_model", "args": [], "return_type": "int"},
        ],
        rate_limit_overrides=overrides,
    )
    notebook_module = _register_fake_notebook_module(monkeypatch)
    notebook_module.limited = lambda: 1
    notebook_module.free = lambda: 2
    notebook_module.train_model = lambda: 3
    monkeypatch.setenv("NOTEBOOK_API_KEY", "key-a,key-b")
    monkeypatch.delenv("NOTEBOOK_API_RATE_LIMIT_PER_MINUTE", raising=False)
    namespace = {}
    exec(compile(code, "<generated>", "exec"), namespace)

    from fastapi.testclient import TestClient

    return TestClient(namespace["app"])


def test_rate_limit_directive_throttles_only_that_endpoint_per_key(monkeypatch):
    """Confirmed missing before this feature: the only rate limit was the
    global per-key NOTEBOOK_API_RATE_LIMIT_PER_MINUTE shared by every endpoint."""
    client = _endpoint_rate_limit_client(monkeypatch, {"limited": 2})
    a = {"X-API-Key": "key-a"}

    assert [client.post("/limited", json={}, headers=a).status_code for _ in range(2)] == [200, 200]
    blocked = client.post("/limited", json={}, headers=a)

    assert blocked.status_code == 429
    assert "Rate limit exceeded for /limited: 2 requests per 60s" in blocked.json()["detail"]
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Limit"] == "2"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert client.post("/free", json={}, headers=a).status_code == 200
    assert client.post("/limited", json={}, headers={"X-API-Key": "key-b"}).status_code == 200


def test_rate_limit_directive_applies_to_background_endpoints(monkeypatch):
    client = _endpoint_rate_limit_client(monkeypatch, {"train_model": 1})
    a = {"X-API-Key": "key-a"}

    assert client.post("/train_model", json={}, headers=a).status_code == 200
    assert client.post("/train_model", json={}, headers=a).status_code == 429


def test_rate_limit_directive_does_not_count_rejected_keys(monkeypatch):
    client = _endpoint_rate_limit_client(monkeypatch, {"limited": 1})

    assert client.post("/limited", json={}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/limited", json={}, headers={"X-API-Key": "key-a"}).status_code == 200


def test_no_rate_limit_directive_leaves_endpoint_signature_unchanged():
    code = generate_fastapi_code([{"name": "free", "args": [], "return_type": "int"}])

    assert "_rl_api_key" not in code


def test_rate_limit_directive_rejections_are_counted_in_metrics(monkeypatch):
    """Confirmed missing before this feature: a per-endpoint 429 was
    counted nowhere, so an operator couldn't tell which quota was biting."""
    client = _endpoint_rate_limit_client(monkeypatch, {"limited": 1})
    a = {"X-API-Key": "key-a"}
    assert client.get("/metrics", headers=a).json()["rate_limited_by_endpoint"] == {}
    assert "endpoint_rate_limited_total" not in client.get("/metrics/prometheus", headers=a).text

    for _ in range(3):
        client.post("/limited", json={}, headers=a)

    assert client.get("/metrics", headers=a).json()["rate_limited_by_endpoint"] == {"/limited": 2}
    text = client.get("/metrics/prometheus", headers=a).text
    assert "# TYPE notebook_api_endpoint_rate_limited_total counter" in text
    assert 'notebook_api_endpoint_rate_limited_total{path="/limited"} 2' in text


def test_rate_limit_directive_is_published_in_openapi(monkeypatch):
    client = _endpoint_rate_limit_client(monkeypatch, {"limited": 7, "train_model": 3})

    paths = client.get("/openapi.json").json()["paths"]

    assert paths["/limited"]["post"]["x-notebook-to-api-rate-limit-per-minute"] == 7
    assert paths["/train_model"]["post"]["x-notebook-to-api-rate-limit-per-minute"] == 3
    assert "x-notebook-to-api-rate-limit-per-minute" not in paths["/free"]["post"]
