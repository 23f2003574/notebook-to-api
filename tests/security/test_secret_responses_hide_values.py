"""API responses and logs never carry a decrypted secret; internal resolution still can."""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.security import secret_vault, secrets as legacy

VALUE = "zx9-DISTINCT-plaintext-5527"
NEW_VALUE = "zx9-ROTATED-plaintext-8841"

app = FastAPI()
app.include_router(legacy.router)
app.include_router(secret_vault.router, prefix="/v2")
client = TestClient(app)

STACKS = [
    pytest.param("/security/secrets", legacy.get_secret_management_service, id="legacy"),
    pytest.param("/v2/security/secrets", secret_vault.get_secret_vault_service, id="vault"),
]


@pytest.mark.parametrize("base, service", STACKS)
def test_no_response_exposes_the_value_but_internal_resolution_does(base, service, caplog):
    name = f"hide-{base.strip('/').replace('/', '-')}"
    caplog.set_level(logging.DEBUG)
    responses = [
        client.post(base, json={"name": name, "secret_type": "Encryption Keys", "value": VALUE}),
        client.post(base, json={"name": name, "secret_type": "Encryption Keys", "value": VALUE}),  # 409
        client.get(f"{base}/{name}"),
        client.post(f"{base}/{name}/rotate", json={"value": NEW_VALUE}),
        client.get(f"{base}/{name}"),
        client.get(f"{base}/missing-{name}"),
        client.post(f"{base}/missing-{name}/rotate", json={"value": NEW_VALUE}),
        client.post(base, json={"name": name + "-bad", "secret_type": "Not A Type", "value": VALUE}),  # 422
    ]
    assert [r.status_code for r in responses] == [200, 409, 200, 200, 200, 404, 404, 422]
    for r in responses:
        assert VALUE not in r.text and NEW_VALUE not in r.text and "value" not in (r.json() if r.status_code == 200 else {})
    assert responses[0].json()["name"] == name  # metadata is still returned
    assert VALUE not in caplog.text and NEW_VALUE not in caplog.text
    # Internal consumers still get the decrypted, current value.
    assert service().retrieve(name).value == NEW_VALUE


@pytest.mark.parametrize("base", [b.values[0] for b in STACKS])
def test_deleted_secret_is_gone_and_delete_response_is_plain(base):
    name = f"del-{base.strip('/').replace('/', '-')}"
    client.post(base, json={"name": name, "secret_type": "Encryption Keys", "value": VALUE})
    r = client.delete(f"{base}/{name}")
    assert r.status_code == 200 and VALUE not in r.text
    assert client.get(f"{base}/{name}").status_code == 404


def test_values_stay_encrypted_at_rest():
    svc = legacy.get_secret_management_service()
    svc.store("rest-check", "Encryption Keys", VALUE)
    assert VALUE.encode() not in repr(svc.__dict__).encode()
    assert svc.retrieve("rest-check").value == VALUE
