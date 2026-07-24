from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from .deployment_governance_event_bus import GovernanceEventBus
    from .deployment_governance_secret_vault import DeploymentSecretVault

# The authentication providers this manager ships with, selectable by
# name via authenticate()'s provider parameter. Not enforced as a
# closed set by authenticate() itself (any name registered via
# register_provider() works identically) — this is the plug-in shape
# DeploymentRolloutPolicyEngine's BUILT_IN_ROLLOUT_POLICIES already
# established, chosen specifically so OAuth/OIDC/SAML providers can be
# added later via register_provider() without modifying this manager.
BUILT_IN_AUTHENTICATION_PROVIDERS: "tuple[str, ...]" = (
    "LOCAL",
    "API_KEY",
    "BEARER_TOKEN",
)

# A provider decides whether (principal, credentials) are valid as of
# now, returning (ok, reason, expires_at): reason is set only when ok
# is False, and expires_at — set only by providers whose credentials
# carry their own expiration (BEARER_TOKEN) — becomes the resulting
# session's expiry. None means the session never expires on its own
# (it can still be revoke()d).
DeploymentAuthenticationProvider = Callable[
    [str, "dict[str, Any]", datetime],
    "tuple[bool, str | None, datetime | None]",
]


@dataclass(frozen=True)
class DeploymentIdentity:
    """
    One successfully authenticated identity: the principal it
    resolved to, which provider vouched for it, and when.
    """

    identity_id: str

    principal: str

    provider: str

    authenticated_at: datetime

    def __post_init__(self) -> None:
        if not self.identity_id:
            raise ValueError("identity_id must not be empty")

        if not self.principal:
            raise ValueError("principal must not be empty")

        if not self.provider:
            raise ValueError("provider must not be empty")

        if self.authenticated_at.tzinfo is None:
            raise ValueError(
                "authenticated_at must be timezone-aware"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "identity_id": self.identity_id,
            "principal": self.principal,
            "provider": self.provider,
            "authenticated_at": self.authenticated_at.isoformat(),
        }


@dataclass(frozen=True)
class AuthenticationResult:
    """
    The immutable outcome of an authentication attempt or a
    validity/status check. identity is set if and only if authenticated
    is True; reason is set if and only if it is False — the same
    allowed/denied invariant RolloutPolicyDecision enforces.
    """

    authenticated: bool

    identity: "DeploymentIdentity | None"

    reason: "str | None"

    def __post_init__(self) -> None:
        if self.authenticated:
            if self.identity is None:
                raise ValueError(
                    "identity must be set when authenticated is True"
                )

            if self.reason is not None:
                raise ValueError(
                    "reason must not be set when authenticated is True"
                )

        else:
            if self.identity is not None:
                raise ValueError(
                    "identity must not be set when authenticated is "
                    "False"
                )

            if self.reason is None:
                raise ValueError(
                    "reason must be set when authenticated is False"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "authenticated": self.authenticated,
            "identity": (
                self.identity.to_dict()
                if self.identity is not None
                else None
            ),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class _Session:
    """
    Internal session-store record backing one DeploymentIdentity.
    Distinct from DeploymentIdentity itself: expires_at and revoked
    are mutable-over-time session bookkeeping that has no place on an
    otherwise-immutable identity record. Replaced wholesale (never
    mutated in place) on revoke(), matching Rollout/JobExecution.
    """

    identity: DeploymentIdentity

    expires_at: "datetime | None"

    revoked: bool


class DeploymentAuthenticationManager:
    """
    Validates deployment identities and issues authenticated execution
    contexts — distinct from DeploymentRBACEngine, which answers
    whether an already-authenticated principal holds a permission, not
    who that principal is or whether its credentials are still good.

    Ships with three built-in authentication providers
    (BUILT_IN_AUTHENTICATION_PROVIDERS): LOCAL (a registered password
    per principal, via register_local_credential), API_KEY (a
    registered key per principal, via register_api_key), and
    BEARER_TOKEN (a registered token per principal, optionally with
    its own expiry, via issue_bearer_token — the vehicle for this
    manager's token-expiration support). Custom providers — OAuth,
    OIDC, SAML — can be added via register_provider() without
    modifying this manager, the same plug-in shape
    DeploymentRolloutPolicyEngine's built-in rollout checks use.

    authenticate() never raises for invalid credentials or an unknown
    provider — it always returns an AuthenticationResult, the same
    "deterministic decision object rather than an exception for a
    normal deny" contract DeploymentRBACEngine.authorize() follows.
    validate() and status() do raise KeyError for a wholly unknown
    identity_id (there is no session to report on), but return a
    denied AuthenticationResult — not an exception — for a known
    identity_id whose session has since expired or been revoked;
    status() is the read-only, API-facing form of the same check
    validate() performs, kept as a separate method since other
    governance services (during the eventual final-bootstrap wiring
    this commit deliberately does not perform yet) are expected to
    call validate() directly rather than through the API.

    Thread-safe: the session store and every credential store is
    guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Callable[[], datetime] | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._sessions: "dict[str, _Session]" = {}

        self._local_credentials: "dict[str, str]" = {}

        self._api_keys: "dict[str, str]" = {}

        self._bearer_tokens: (
            "dict[str, tuple[str, datetime | None]]"
        ) = {}

        self._providers: "dict[str, DeploymentAuthenticationProvider]" = (
            dict(self._built_in_providers())
        )

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

    def register_provider(
        self, name: str, provider: DeploymentAuthenticationProvider
    ) -> None:
        """
        Register (or replace) a named authentication provider — the
        extension point OAuth/OIDC/SAML support is expected to use.
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            self._providers[name] = provider

    def register_local_credential(
        self, principal: str, password: str
    ) -> None:
        """
        Register (or replace) principal's password for the LOCAL
        provider.
        """

        if not principal:
            raise ValueError("principal must not be empty")

        with self._lock:
            self._local_credentials[principal] = password

    def register_api_key(self, api_key: str, principal: str) -> None:
        """
        Register (or replace) api_key as belonging to principal, for
        the API_KEY provider.
        """

        if not api_key:
            raise ValueError("api_key must not be empty")

        with self._lock:
            self._api_keys[api_key] = principal

    def issue_bearer_token(
        self,
        principal: str,
        token: str,
        *,
        expires_at: "datetime | None" = None,
    ) -> None:
        """
        Register (or replace) token as belonging to principal, for
        the BEARER_TOKEN provider. With expires_at given, the token —
        and every session authenticate() issues from it — stops being
        valid at that instant; omitting it means the token itself
        never expires on its own (though a session issued from it can
        still be revoke()d).
        """

        if not principal:
            raise ValueError("principal must not be empty")

        if not token:
            raise ValueError("token must not be empty")

        with self._lock:
            self._bearer_tokens[token] = (principal, expires_at)

    def register_local_credential_from_vault(
        self,
        principal: str,
        vault: "DeploymentSecretVault",
        secret_name: str,
    ) -> None:
        """
        Convenience wrapper: fetch secret_name's current value from
        vault and register it as principal's LOCAL password via
        register_local_credential — the same narrow "spare a caller
        already holding both objects some boilerplate" integration
        DeploymentRBACEngine.authorize_identity established for RBAC.
        This manager still does not consult any secret vault
        automatically, or any other provider integration beyond
        LOCAL/API_KEY/BEARER_TOKEN — those remain standalone, as
        established when this manager was introduced.

        Raises KeyError if secret_name is not stored in vault.
        """

        self.register_local_credential(principal, vault.fetch(secret_name))

    def authenticated_principal(self, identity_id: str) -> str:
        """
        Return identity_id's principal if its session is currently
        valid (validate()'s success path) — the small bridge other
        governance services (starting with
        DeploymentApprovalEngine) can use to turn an identity_id into
        a trusted principal string before consulting RBAC, instead of
        trusting a bare principal string handed to them directly.

        Raises KeyError if identity_id was never issued by
        authenticate() (propagated from validate()), or
        PermissionError if it was but its session is no longer valid
        (revoked or expired).
        """

        result = self.validate(identity_id)

        if not result.authenticated:
            raise PermissionError(result.reason)

        return result.identity.principal

    def authenticate(
        self,
        principal: str,
        provider: str,
        credentials: "dict[str, Any] | None" = None,
    ) -> AuthenticationResult:
        """
        Authenticate principal against provider using credentials (a
        provider-specific mapping — e.g. {"password": ...} for LOCAL),
        issuing a new DeploymentIdentity and session on success.

        Raises ValueError if principal or provider is empty. Never
        raises for invalid credentials or an unrecognized provider
        name — either denies with a reason instead.
        """

        if not principal:
            raise ValueError("principal must not be empty")

        if not provider:
            raise ValueError("provider must not be empty")

        credentials = credentials or {}
        now = self._clock()

        with self._lock:
            check = self._providers.get(provider)

        if check is None:
            return self._deny(
                principal,
                f"unknown authentication provider '{provider}'",
            )

        ok, reason, expires_at = check(principal, credentials, now)

        if not ok:
            return self._deny(
                principal, reason or "authentication failed"
            )

        identity = DeploymentIdentity(
            identity_id=str(uuid4()),
            principal=principal,
            provider=provider,
            authenticated_at=now,
        )

        with self._lock:
            self._sessions[identity.identity_id] = _Session(
                identity=identity,
                expires_at=expires_at,
                revoked=False,
            )

        result = AuthenticationResult(
            authenticated=True, identity=identity, reason=None
        )

        self._publish(
            "authentication_succeeded",
            identity.identity_id,
            result.to_dict(),
        )

        return result

    def validate(self, identity_id: str) -> AuthenticationResult:
        """
        Check whether identity_id's session is still good — neither
        revoked nor (for a session issued from an expiring credential)
        past its expiry — re-evaluated fresh against the current clock
        reading on every call, so a session that was valid a moment
        ago can validate as expired now without any explicit revoke().

        Raises KeyError if identity_id was never issued by
        authenticate().
        """

        with self._lock:
            session = self._sessions.get(identity_id)

            if session is None:
                raise KeyError(
                    f"identity '{identity_id}' is not registered"
                )

            if session.revoked:
                return AuthenticationResult(
                    authenticated=False, identity=None,
                    reason="identity has been revoked",
                )

            if (
                session.expires_at is not None
                and self._clock() >= session.expires_at
            ):
                return AuthenticationResult(
                    authenticated=False, identity=None,
                    reason="token has expired",
                )

            return AuthenticationResult(
                authenticated=True, identity=session.identity,
                reason=None,
            )

    def status(self, identity_id: str) -> AuthenticationResult:
        """
        Return identity_id's current session state — the read-only,
        API-facing equivalent of validate(). Raises KeyError if
        identity_id was never issued by authenticate().
        """

        return self.validate(identity_id)

    def revoke(self, identity_id: str) -> None:
        """
        Revoke identity_id's session, so every subsequent validate()/
        status() call denies it. Idempotent: revoking an already-
        revoked identity is a no-op.

        Raises KeyError if identity_id was never issued by
        authenticate().
        """

        with self._lock:
            session = self._sessions.get(identity_id)

            if session is None:
                raise KeyError(
                    f"identity '{identity_id}' is not registered"
                )

            already_revoked = session.revoked

            if not already_revoked:
                self._sessions[identity_id] = replace(
                    session, revoked=True
                )

        if not already_revoked:
            self._publish("authentication_revoked", identity_id, {})

    def clear(self) -> None:
        """
        Remove every session and every registered credential (local
        passwords, API keys, bearer tokens). Registered custom
        providers are left untouched — they are configuration, not
        session state.
        """

        with self._lock:
            self._sessions.clear()
            self._local_credentials.clear()
            self._api_keys.clear()
            self._bearer_tokens.clear()

    def _deny(self, source: str, reason: str) -> AuthenticationResult:
        result = AuthenticationResult(
            authenticated=False, identity=None, reason=reason
        )

        self._publish("authentication_failed", source, result.to_dict())

        return result

    def _built_in_providers(
        self,
    ) -> "dict[str, DeploymentAuthenticationProvider]":
        return {
            "LOCAL": self._authenticate_local,
            "API_KEY": self._authenticate_api_key,
            "BEARER_TOKEN": self._authenticate_bearer_token,
        }

    def _authenticate_local(
        self, principal: str, credentials: "dict[str, Any]", now: datetime
    ) -> "tuple[bool, str | None, datetime | None]":
        with self._lock:
            expected = self._local_credentials.get(principal)

        if expected is None:
            return False, f"unknown local principal '{principal}'", None

        if credentials.get("password") != expected:
            return False, "invalid credentials", None

        return True, None, None

    def _authenticate_api_key(
        self, principal: str, credentials: "dict[str, Any]", now: datetime
    ) -> "tuple[bool, str | None, datetime | None]":
        api_key = credentials.get("api_key")

        if not api_key:
            return False, "api_key is required", None

        with self._lock:
            owner = self._api_keys.get(api_key)

        if owner is None or owner != principal:
            return False, "invalid credentials", None

        return True, None, None

    def _authenticate_bearer_token(
        self, principal: str, credentials: "dict[str, Any]", now: datetime
    ) -> "tuple[bool, str | None, datetime | None]":
        token = credentials.get("token")

        if not token:
            return False, "token is required", None

        with self._lock:
            entry = self._bearer_tokens.get(token)

        if entry is None:
            return False, "invalid credentials", None

        owner, expires_at = entry

        if owner != principal:
            return False, "invalid credentials", None

        if expires_at is not None and now >= expires_at:
            return False, "token has expired", None

        return True, None, expires_at

    def _publish(
        self,
        event_type: str,
        source: str,
        payload: "dict[str, Any] | None" = None,
    ) -> None:
        if self._event_bus is None:
            return

        self._event_bus.publish(
            event_type, source=source, payload=payload
        )


def build_default_governance_authentication_manager() -> (
    DeploymentAuthenticationManager
):
    """
    Build the process-wide deployment authentication manager, wired to
    the process-wide governance event bus.

    Deliberately not wired into the rollout manager, rollout policy
    engine, rollout dashboard, or any other governance service yet —
    that integration is scoped to the final bootstrap commit. This
    manager exists standalone for now: authenticate(), validate(),
    revoke(), and status() are fully usable, just not yet consulted by
    anything else in this codebase.
    """

    from .deployment_governance_event_bus import get_event_bus

    return DeploymentAuthenticationManager(event_bus=get_event_bus())


# Shared for the lifetime of the process: sessions issued through the
# API need to be validated and revoked identically by every caller,
# which a persistence runtime built fresh per request cannot provide
# on its own.
_authentication_manager = (
    build_default_governance_authentication_manager()
)


def get_authentication_manager() -> DeploymentAuthenticationManager:
    """
    Return the process-wide deployment authentication manager.
    """

    return _authentication_manager
