from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, MutableMapping, Protocol

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus

# The secret vault providers this vault ships with, selectable by name
# via store()'s provider parameter (and DeploymentSecretVault's own
# default_provider). Not enforced as a closed set — register_provider()
# adds any name — this is the plug-in shape
# BUILT_IN_AUTHENTICATION_PROVIDERS already established, chosen
# specifically so a real secrets backend (HashiCorp Vault, AWS Secrets
# Manager, Azure Key Vault, ...) can be added later via
# register_provider() without changing DeploymentSecretVault's own
# API.
BUILT_IN_SECRET_VAULT_PROVIDERS: "tuple[str, ...]" = (
    "InMemoryVault",
    "EnvironmentVariables",
)


class SecretVaultProvider(Protocol):
    """
    The minimal shape a secret storage backend must implement.
    Deliberately narrow — put/get/delete/exists on a raw string value,
    nothing about versioning or expiry — so that a future real
    backend (which may not expose versions or TTLs the same way two
    different backends do) only ever has to implement this same small
    surface. DeploymentSecretVault itself is what layers versioning,
    metadata, and events on top, identically regardless of which
    provider is actually holding the bytes.
    """

    def put(self, name: str, value: str) -> None:
        ...

    def get(self, name: str) -> "str | None":
        ...

    def delete(self, name: str) -> None:
        ...

    def exists(self, name: str) -> bool:
        ...


class InMemoryVaultProvider:
    """
    A process-local, in-memory secret store. The default provider —
    useful for tests and for a governance runtime that has no external
    secrets backend configured yet.
    """

    def __init__(self) -> None:
        self._values: "dict[str, str]" = {}

    def put(self, name: str, value: str) -> None:
        self._values[name] = value

    def get(self, name: str) -> "str | None":
        return self._values.get(name)

    def delete(self, name: str) -> None:
        self._values.pop(name, None)

    def exists(self, name: str) -> bool:
        return name in self._values


class EnvironmentVariablesProvider:
    """
    A secret store backed by process environment variables. env
    defaults to os.environ itself; tests (and any caller that does not
    want to touch the real process environment) should pass their own
    mutable mapping instead.
    """

    def __init__(
        self, *, env: "MutableMapping[str, str] | None" = None
    ) -> None:
        self._env: "MutableMapping[str, str]" = (
            os.environ if env is None else env
        )

    def put(self, name: str, value: str) -> None:
        self._env[name] = value

    def get(self, name: str) -> "str | None":
        return self._env.get(name)

    def delete(self, name: str) -> None:
        if name in self._env:
            del self._env[name]

    def exists(self, name: str) -> bool:
        return name in self._env


@dataclass(frozen=True)
class SecretReference:
    """
    An immutable pointer to one stored secret's current version —
    never the secret's value itself.
    """

    name: str

    version: str

    provider: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        if not self.version:
            raise ValueError("version must not be empty")

        if not self.provider:
            raise ValueError("provider must not be empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "provider": self.provider,
        }


@dataclass(frozen=True)
class SecretMetadata:
    """
    An immutable, value-free snapshot of one secret's lifecycle state
    — safe to log, publish as an event, or return over the API,
    unlike the secret value itself.
    """

    created_at: datetime

    expires_at: "datetime | None"

    rotated: bool

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")

        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at": self.created_at.isoformat(),
            "expires_at": (
                self.expires_at.isoformat()
                if self.expires_at is not None
                else None
            ),
            "rotated": self.rotated,
        }


class DeploymentSecretVault:
    """
    Securely retrieves deployment secrets from a pluggable provider
    (BUILT_IN_SECRET_VAULT_PROVIDERS: InMemoryVault, EnvironmentVariables
    — a real backend like HashiCorp Vault, AWS Secrets Manager, or
    Azure Key Vault can be added later via register_provider() without
    changing this class's own API), while itself owning versioning,
    metadata, and event publication uniformly across whichever
    provider is actually storing the bytes.

    Every provider stores exactly one current value per secret name;
    this vault layers a version counter and SecretMetadata
    (created_at/expires_at/rotated) on top, incrementing the version
    and flipping rotated to True on rotate(). fetch() always returns
    the current version — there is no historical-version retrieval,
    matching how store()/fetch()/exists()/delete()/rotate()/metadata()
    are all name-keyed, not name-and-version-keyed.

    Secret values themselves never appear in SecretReference,
    SecretMetadata, or any published event payload or exception
    message — those types structurally cannot hold one, which is what
    makes "never log secret values" true rather than merely
    documented, the same reasoning behind GovernanceEvent wrapping its
    payload in a read-only mapping.

    Thread-safe: the reference and metadata registries are guarded by
    an internal lock; provider calls themselves happen outside the
    lock, matching DeploymentAuthenticationManager's own provider
    calls.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        default_provider: str = "InMemoryVault",
        environment: "MutableMapping[str, str] | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._providers: "dict[str, SecretVaultProvider]" = {
            "InMemoryVault": InMemoryVaultProvider(),
            "EnvironmentVariables": EnvironmentVariablesProvider(
                env=environment
            ),
        }

        self._default_provider = default_provider

        self._references: "dict[str, SecretReference]" = {}

        self._metadata: "dict[str, SecretMetadata]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register_provider(
        self, name: str, provider: SecretVaultProvider
    ) -> None:
        """
        Register (or replace) a named secret vault provider — the
        extension point a real secrets backend is expected to use.
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            self._providers[name] = provider

    def store(
        self,
        name: str,
        value: str,
        *,
        provider: "str | None" = None,
        expires_at: "datetime | None" = None,
    ) -> SecretReference:
        """
        Store value as secret name's first version, via provider (the
        vault's default_provider if omitted).

        Raises ValueError if name is already stored (rotate() is how
        an existing secret's value changes) or if provider does not
        name a registered provider.
        """

        if not name:
            raise ValueError("name must not be empty")

        provider_name = provider or self._default_provider

        with self._lock:
            if name in self._references:
                raise ValueError(f"secret '{name}' is already stored")

            backend = self._providers.get(provider_name)

            if backend is None:
                raise ValueError(
                    f"unknown secret vault provider '{provider_name}'"
                )

            now = self._clock()

            reference = SecretReference(
                name=name, version="1", provider=provider_name
            )

            metadata = SecretMetadata(
                created_at=now, expires_at=expires_at, rotated=False
            )

            self._references[name] = reference
            self._metadata[name] = metadata

        backend.put(name, value)

        self._publish("secret_stored", name, reference.to_dict())

        return reference

    def fetch(self, name: str) -> str:
        """
        Return secret name's current value.

        Raises KeyError if name is not stored.
        """

        with self._lock:
            reference = self._references.get(name)

            if reference is None:
                raise KeyError(f"secret '{name}' is not stored")

            backend = self._providers[reference.provider]

        value = backend.get(name)

        if value is None:
            raise KeyError(f"secret '{name}' is not stored")

        self._publish("secret_retrieved", name, {})

        return value

    def exists(self, name: str) -> bool:
        """
        Return whether name is currently stored.
        """

        with self._lock:
            return name in self._references

    def delete(self, name: str) -> None:
        """
        Remove secret name from both this vault's registry and its
        underlying provider.

        Raises KeyError if name is not stored.
        """

        with self._lock:
            reference = self._references.get(name)

            if reference is None:
                raise KeyError(f"secret '{name}' is not stored")

            backend = self._providers[reference.provider]

            del self._references[name]
            del self._metadata[name]

        backend.delete(name)

        self._publish("secret_deleted", name, {})

    def rotate(
        self,
        name: str,
        new_value: str,
        *,
        expires_at: "datetime | None" = None,
    ) -> SecretReference:
        """
        Replace secret name's value with new_value, incrementing its
        version and marking its metadata rotated. With expires_at
        given, replaces the secret's expiry; omitting it preserves
        whatever expiry (or lack of one) the secret already had.

        Raises KeyError if name is not stored.
        """

        with self._lock:
            reference = self._references.get(name)

            if reference is None:
                raise KeyError(f"secret '{name}' is not stored")

            backend = self._providers[reference.provider]

            new_reference = replace(
                reference, version=str(int(reference.version) + 1)
            )

            previous_metadata = self._metadata[name]

            new_metadata = SecretMetadata(
                created_at=previous_metadata.created_at,
                expires_at=(
                    expires_at
                    if expires_at is not None
                    else previous_metadata.expires_at
                ),
                rotated=True,
            )

            self._references[name] = new_reference
            self._metadata[name] = new_metadata

        backend.put(name, new_value)

        self._publish(
            "secret_rotated", name, new_reference.to_dict()
        )

        return new_reference

    def metadata(self, name: str) -> SecretMetadata:
        """
        Return secret name's current metadata.

        Raises KeyError if name is not stored.
        """

        with self._lock:
            metadata = self._metadata.get(name)

            if metadata is None:
                raise KeyError(f"secret '{name}' is not stored")

            return metadata

    def reference(self, name: str) -> SecretReference:
        """
        Return secret name's current reference (its version and which
        provider holds it).

        Raises KeyError if name is not stored.
        """

        with self._lock:
            reference = self._references.get(name)

            if reference is None:
                raise KeyError(f"secret '{name}' is not stored")

            return reference

    def clear(self) -> None:
        """
        Remove every stored secret from both this vault's registry and
        every underlying provider it was stored through.
        """

        with self._lock:
            references = list(self._references.values())
            self._references.clear()
            self._metadata.clear()

        for reference in references:
            self._providers[reference.provider].delete(reference.name)

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, object] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_secret_vault() -> DeploymentSecretVault:
    """
    Build the process-wide deployment secret vault, wired to the
    process-wide governance event bus.
    """

    from .deployment_governance_event_bus import get_event_bus

    return DeploymentSecretVault(event_bus=get_event_bus())


# Shared for the lifetime of the process: secrets stored through the
# API need to be fetchable/rotatable/deletable identically by every
# caller, which a persistence runtime built fresh per request cannot
# provide on its own.
_secret_vault = build_default_governance_secret_vault()


def get_secret_vault() -> DeploymentSecretVault:
    """
    Return the process-wide deployment secret vault.
    """

    return _secret_vault
