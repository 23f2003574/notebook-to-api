from __future__ import annotations

import json
from io import StringIO

from backend.observability.deployment_governance_provider_registry_cli import (
    run_deployment_governance_provider_list,
    run_deployment_governance_provider_show,
)


def setup_env(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_PERSISTENCE_BACKEND", "sqlite"
    )
    monkeypatch.setenv(
        "NOTEBOOK2API_GOVERNANCE_DATABASE_PATH",
        str(tmp_path / name),
    )


def test_list_includes_every_default_provider(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-list.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_list(
        stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "email: EmailProvider" in output
    assert "slack: SlackProvider" in output
    assert "webhook: WebhookProvider" in output


def test_list_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-list-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_list(
        json_output=True, stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert {
        "channel_type": "email",
        "provider_name": "EmailProvider",
    } in payload


def test_show_returns_registered_provider(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-show.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_show(
        channel_type="slack", stdout=stdout, stderr=StringIO()
    )

    assert exit_code == 0
    output = stdout.getvalue()
    assert "Channel type: slack" in output
    assert "Provider: SlackProvider" in output


def test_show_json(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-show-json.db")

    stdout = StringIO()

    exit_code = run_deployment_governance_provider_show(
        channel_type="webhook",
        json_output=True,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "channel_type": "webhook",
        "provider_name": "WebhookProvider",
    }


def test_show_fails_for_invalid_channel_type(monkeypatch, tmp_path) -> None:
    setup_env(monkeypatch, tmp_path, "providers-show-invalid.db")

    stderr = StringIO()

    exit_code = run_deployment_governance_provider_show(
        channel_type="not-a-real-channel-type",
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 2
    assert "could not be completed" in stderr.getvalue()
