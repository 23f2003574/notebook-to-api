from __future__ import annotations

from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from backend.observability.deployment_governance_notification_channels import (
    GovernanceIntegrityNotificationChannelType,
)
from backend.observability.deployment_governance_persistence import (
    DeploymentGovernancePersistenceConfig,
    build_deployment_governance_persistence,
)
from backend.observability.deployment_governance_provider_authentication import (
    GovernanceIntegrityAuthenticationContext,
    GovernanceIntegrityAuthenticationType,
    GovernanceIntegrityProviderAuthenticationService,
)
from backend.observability.deployment_governance_provider_configuration import (
    GovernanceIntegrityProviderConfigurationService,
    InMemoryGovernanceIntegrityProviderConfigurationRepository,
)
from backend.observability.deployment_governance_provider_registry import (
    GovernanceIntegrityProviderRegistry,
)
from backend.observability.deployment_governance_provider_secrets import (
    GovernanceIntegrityProviderSecretsService,
    InMemoryGovernanceIntegrityProviderSecretsRepository,
)

BASE_TIME = datetime(2026, 7, 15, 23, 0, 0, tzinfo=timezone.utc)

EMAIL = GovernanceIntegrityNotificationChannelType.EMAIL
SLACK = GovernanceIntegrityNotificationChannelType.SLACK
WEBHOOK = GovernanceIntegrityNotificationChannelType.WEBHOOK


class FakeProvider:
    """
    Minimal test double exposing only what the authentication
    service needs: a declared authentication type. It is never
    resolved for delivery, capabilities, or health in these tests.
    """

    def __init__(self, authentication_type) -> None:
        self._authentication_type = authentication_type

    def authentication_type(self):
        return self._authentication_type


class Harness:
    def __init__(self) -> None:
        self.registry = GovernanceIntegrityProviderRegistry()

        self.configuration_service = (
            GovernanceIntegrityProviderConfigurationService(
                InMemoryGovernanceIntegrityProviderConfigurationRepository(),
                self.registry,
                clock=lambda: BASE_TIME,
            )
        )

        self.secrets_service = GovernanceIntegrityProviderSecretsService(
            InMemoryGovernanceIntegrityProviderSecretsRepository(),
            self.registry,
            clock=lambda: BASE_TIME,
        )

        self.service = GovernanceIntegrityProviderAuthenticationService(
            self.configuration_service, self.secrets_service, self.registry
        )

    def register(self, channel_type, authentication_type) -> None:
        self.registry.register(
            channel_type, FakeProvider(authentication_type)
        )


# --- Model ---------------------------------------------------------------


def test_context_mappings_are_immutable() -> None:
    context = GovernanceIntegrityAuthenticationContext(
        authentication_type=GovernanceIntegrityAuthenticationType.API_KEY,
        headers={"X-API-Key": "abc123"},
        parameters={"foo": "bar"},
    )

    assert isinstance(context.headers, MappingProxyType)
    assert isinstance(context.parameters, MappingProxyType)

    with pytest.raises(TypeError):
        context.headers["X-API-Key"] = "xyz789"

    with pytest.raises(TypeError):
        context.parameters["foo"] = "baz"


def test_context_to_dict() -> None:
    context = GovernanceIntegrityAuthenticationContext(
        authentication_type=GovernanceIntegrityAuthenticationType.BASIC,
        headers={"Authorization": "Basic xyz"},
        parameters={},
    )

    assert context.to_dict() == {
        "authentication_type": "basic",
        "headers": {"Authorization": "Basic xyz"},
        "parameters": {},
    }


# --- NONE --------------------------------------------------------------


def test_none_authentication_has_no_headers() -> None:
    harness = Harness()
    harness.register(EMAIL, GovernanceIntegrityAuthenticationType.NONE)

    context = harness.service.build(EMAIL)

    assert (
        context.authentication_type
        is GovernanceIntegrityAuthenticationType.NONE
    )
    assert dict(context.headers) == {}


# --- API_KEY -------------------------------------------------------------


def test_api_key_authentication_builds_header() -> None:
    harness = Harness()
    harness.register(WEBHOOK, GovernanceIntegrityAuthenticationType.API_KEY)

    harness.secrets_service.create(WEBHOOK, {"api_key": "abc123"})

    context = harness.service.build(WEBHOOK)

    assert context.headers["X-API-Key"] == "abc123"


def test_api_key_authentication_raises_when_secret_missing() -> None:
    harness = Harness()
    harness.register(WEBHOOK, GovernanceIntegrityAuthenticationType.API_KEY)

    with pytest.raises(ValueError):
        harness.service.build(WEBHOOK)


# --- BEARER_TOKEN --------------------------------------------------------


def test_bearer_token_authentication_builds_header() -> None:
    harness = Harness()
    harness.register(
        SLACK, GovernanceIntegrityAuthenticationType.BEARER_TOKEN
    )

    harness.secrets_service.create(SLACK, {"token": "xoxb-123"})

    context = harness.service.build(SLACK)

    assert context.headers["Authorization"].startswith("Bearer")
    assert context.headers["Authorization"] == "Bearer xoxb-123"


def test_bearer_token_authentication_raises_when_secret_missing() -> None:
    harness = Harness()
    harness.register(
        SLACK, GovernanceIntegrityAuthenticationType.BEARER_TOKEN
    )

    with pytest.raises(ValueError):
        harness.service.build(SLACK)


# --- BASIC ---------------------------------------------------------------


def test_basic_authentication_builds_header() -> None:
    harness = Harness()
    harness.register(EMAIL, GovernanceIntegrityAuthenticationType.BASIC)

    harness.secrets_service.create(
        EMAIL, {"username": "ops", "password": "hunter2"}
    )

    context = harness.service.build(EMAIL)

    assert "Authorization" in context.headers
    assert context.headers["Authorization"].startswith("Basic")


def test_basic_authentication_raises_when_username_missing() -> None:
    harness = Harness()
    harness.register(EMAIL, GovernanceIntegrityAuthenticationType.BASIC)

    harness.secrets_service.create(EMAIL, {"password": "hunter2"})

    with pytest.raises(ValueError):
        harness.service.build(EMAIL)


def test_basic_authentication_raises_when_password_missing() -> None:
    harness = Harness()
    harness.register(EMAIL, GovernanceIntegrityAuthenticationType.BASIC)

    harness.secrets_service.create(EMAIL, {"username": "ops"})

    with pytest.raises(ValueError):
        harness.service.build(EMAIL)


# --- Missing provider ----------------------------------------------------


def test_build_raises_for_unregistered_channel_type() -> None:
    harness = Harness()

    with pytest.raises(LookupError):
        harness.service.build(EMAIL)


# --- Runtime ---------------------------------------------------------------


def test_runtime_builds_none_authentication_for_default_email_provider(
    tmp_path,
) -> None:
    """
    The default EmailProvider requires no secrets, so its
    authentication context should build successfully out of the box.
    """

    runtime = build_deployment_governance_persistence(
        DeploymentGovernancePersistenceConfig.sqlite(
            tmp_path / "provider-authentication-runtime.db"
        )
    )

    context = (
        runtime.build_integrity_provider_authentication_service().build(
            EMAIL
        )
    )

    assert (
        context.authentication_type
        is GovernanceIntegrityAuthenticationType.NONE
    )


def test_runtime_builds_api_key_authentication_for_webhook_provider(
    tmp_path,
) -> None:
    runtime = build_deployment_governance_persistence()

    runtime.build_integrity_provider_secrets_service().create(
        WEBHOOK, {"api_key": "abc123"}
    )

    context = (
        runtime.build_integrity_provider_authentication_service().build(
            WEBHOOK
        )
    )

    assert (
        context.authentication_type
        is GovernanceIntegrityAuthenticationType.API_KEY
    )
    assert context.headers["X-API-Key"] == "abc123"
