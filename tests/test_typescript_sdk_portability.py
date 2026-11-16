"""The generated TypeScript client must bundle for browsers and type-check without @types/node:
Node's crypto is reached lazily, and only by verifyWebhookSignature."""
import hashlib
import hmac
import json
import re
import shutil
import subprocess

import pytest

from backend.exporters.sdk_generator import generate_typescript_sdk

SPEC = {
    "openapi": "3.1.0", "info": {"title": "t", "version": "1"},
    "paths": {"/add": {"post": {
        "operationId": "add", "x-notebook-to-api-return-type": "int",
        "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/AddRequest"}}}},
        "responses": {"200": {"description": "ok"}},
    }}},
    "components": {"schemas": {"AddRequest": {
        "type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}}}}},
}


@pytest.fixture(scope="module")
def client_ts(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tssdk")
    spec = tmp / "openapi.json"
    spec.write_text(json.dumps(SPEC))
    out = tmp / "client.ts"
    generate_typescript_sdk(str(spec), str(out))
    return out


def test_no_static_node_import_or_node_only_types(client_ts):
    text = client_ts.read_text()
    assert 'from "node:crypto"' not in text
    assert not re.search(r"\bBuffer\b", text)
    assert "process.env.NOTEBOOK_API_KEY ??" not in text  # unguarded: ReferenceError in browsers


@pytest.mark.skipif(not (shutil.which("tsc") and shutil.which("node")), reason="needs tsc and node")
def test_compiles_strictly_and_runs_with_and_without_node_globals(client_ts):
    tmp = client_ts.parent
    compiled = subprocess.run(
        ["tsc", "--strict", "--target", "es2020", "--lib", "es2020,dom", "--module", "commonjs",
         "--ignoreDeprecations", "6.0", str(client_ts)],
        capture_output=True, text=True, timeout=120,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    body = '{"a":1}'
    signature = "sha256=" + hmac.new(b"s3", body.encode(), hashlib.sha256).hexdigest()
    script = f"""
const c = require({str(tmp / 'client.js')!r});
const ok = c.verifyWebhookSignature({body!r}, {signature!r}, ["old", "s3"]);
const bad = c.verifyWebhookSignature({body!r}, {signature!r}, "nope");
globalThis.process = undefined;  // a browser has no process
new c.NotebookAPIClient("http://localhost");
console.log(JSON.stringify([ok, bad]));
"""
    ran = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout) == [True, False]
