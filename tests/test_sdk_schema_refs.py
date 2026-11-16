"""Request fields typed by a component schema ($ref / allOf) keep that type in
both generated SDKs instead of collapsing to Any / unknown."""
import json

import pytest

from backend.exporters.sdk_generator import generate_python_sdk, generate_typescript_sdk

SCHEMAS = {
    "Color": {"type": "string", "enum": ["red", "blue"], "title": "Color"},
    "Pt": {
        "type": "object", "title": "Pt", "required": ["x", "y"],
        "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "c": {"$ref": "#/components/schemas/Color"}},
    },
    "Node": {
        "type": "object", "title": "Node",
        "properties": {"next": {"$ref": "#/components/schemas/Node"}},
    },
    "UseRequest": {
        "type": "object", "title": "UseRequest", "required": ["color", "pts", "one", "tree"],
        "properties": {
            "color": {"$ref": "#/components/schemas/Color", "description": "d"},
            "pts": {"type": "array", "items": {"$ref": "#/components/schemas/Pt"}},
            "one": {"allOf": [{"$ref": "#/components/schemas/Pt"}]},
            "maybe": {"anyOf": [{"$ref": "#/components/schemas/Color"}, {"type": "null"}]},
            "tree": {"$ref": "#/components/schemas/Node"},
            "gone": {"$ref": "#/components/schemas/Missing"},
        },
    },
}
SPEC = {
    "openapi": "3.1.0", "info": {"title": "t", "version": "1"},
    "paths": {"/use": {"post": {
        "operationId": "use", "x-notebook-to-api-return-type": "int",
        "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/UseRequest"}}}},
        "responses": {"200": {"description": "ok"}},
    }}},
    "components": {"schemas": SCHEMAS},
}


@pytest.fixture(scope="module")
def sdks(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("refs")
    spec = tmp / "openapi.json"
    spec.write_text(json.dumps(SPEC))
    generate_python_sdk(str(spec), str(tmp / "c.py"))
    generate_typescript_sdk(str(spec), str(tmp / "c.ts"))
    return (tmp / "c.py").read_text(), (tmp / "c.ts").read_text()


def test_python_sdk_resolves_refs(sdks):
    code = sdks[0]
    assert "color: Literal['red', 'blue']" in code
    assert "pts: List[Dict[str, Any]]" in code
    assert "one: Dict[str, Any]" in code
    assert "maybe: Optional[Literal['red', 'blue']]" in code
    compile(code, "c.py", "exec")


def test_typescript_sdk_resolves_refs(sdks):
    ts = sdks[1]
    assert 'color: "red" | "blue";' in ts
    assert '({ "x": number; "y": number; "c"?: "red" | "blue" })[]' in ts
    assert 'one: { "x": number; "y": number; "c"?: "red" | "blue" };' in ts
    assert 'maybe?: "red" | "blue" | null;' in ts


def test_cyclic_and_missing_refs_fall_back_without_recursing(sdks):
    code, ts = sdks
    assert "tree: Dict[str, Any]" in code and "gone: Any" in code
    assert '"next"?: unknown' in ts and "gone?: unknown;" in ts
