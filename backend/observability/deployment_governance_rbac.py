from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_audit import GovernanceAuditService
    from .deployment_governance_authentication import DeploymentIdentity
    from .deployment_governance_event_bus import GovernanceEventBus

# The permission vocabulary this engine understands. Not enforced as a
# closed set by register_role() itself (any non-empty permission
# string is accepted, matching how ROLLOUT_POLICY_ACTIONS documents a
# vocabulary without the rollout policy engine enforcing membership) —
# this is what the built-in roles below are assembled from and what
# GET /governance/security/roles is expected to advertise.
BUILT_IN_DEPLOYMENT_PERMISSIONS: "tuple[str, ...]" = (
    "deployment.read",
    "deployment.deploy",
    "deployment.rollback",
    "deployment.approve",
    "deployment.cancel",
    "deployment.manage",
    "policy.manage",
    "audit.read",
    "security.manage",
)

# The built-in roles registered into every new engine at construction
# time (and restored by clear()) — a deployment governance runtime
# should never be left with no roles at all, the same reasoning
# behind rebuilding these on clear() rather than emptying the
# registry outright.
BUILT_IN_DEPLOYMENT_ROLES: "dict[str, frozenset[str]]" = {
    "Administrator": frozenset(BUILT_IN_DEPLOYMENT_PERMISSIONS),
    "Release Manager": frozenset(
        {
            "deployment.read",
            "deployment.deploy",
            "deployment.rollback",
            "deployment.approve",
            "deployment.cancel",
        }
    ),
    "Developer": frozenset({"deployment.read", "deployment.deploy"}),
    "Operator": frozenset(
        {"deployment.read", "deployment.rollback", "deployment.cancel"}
    ),
    "Auditor": frozenset({"deployment.read", "audit.read"}),
    "Security Officer": frozenset(
        {
            "deployment.read",
            "security.manage",
            "policy.manage",
            "audit.read",
        }
    ),
    "Read Only": frozenset({"deployment.read"}),
}


@dataclass(frozen=True)
class DeploymentRole:
    """
    A named, immutable set of permissions.
    """

    name: str

    permissions: "frozenset[str]"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

        object.__setattr__(
            self, "permissions", frozenset(self.permissions)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "permissions": sorted(self.permissions),
        }


@dataclass(frozen=True)
class DeploymentPrincipal:
    """
    One principal's current role assignment — a user, service
    account, or any other actor deployment operations are attributed
    to.
    """

    principal_id: str

    roles: "tuple[str, ...]"

    def __post_init__(self) -> None:
        if not self.principal_id:
            raise ValueError("principal_id must not be empty")

        object.__setattr__(self, "roles", tuple(self.roles))

    def to_dict(self) -> dict[str, object]:
        return {
            "principal_id": self.principal_id,
            "roles": list(self.roles),
        }


@dataclass(frozen=True)
class AuthorizationDecision:
    """
    The immutable outcome of evaluating one permission for one
    principal, against the roles assigned to it at evaluation time.
    """

    principal_id: str

    permission: str

    allowed: bool

    roles: "tuple[str, ...]"

    evaluated_at: datetime

    def __post_init__(self) -> None:
        if not self.principal_id:
            raise ValueError("principal_id must not be empty")

        if not self.permission:
            raise ValueError("permission must not be empty")

        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")

        object.__setattr__(self, "roles", tuple(self.roles))

    def to_dict(self) -> dict[str, object]:
        return {
            "principal_id": self.principal_id,
            "permission": self.permission,
            "allowed": self.allowed,
            "roles": list(self.roles),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


class DeploymentRBACEngine:
    """
    Centralizes authorization for deployment governance operations:
    named roles (a permission set) can be assigned to principals, and
    authorize() answers whether a given principal currently holds a
    given permission — the union of every permission granted by every
    role assigned to it (permission inheritance across multiple
    roles). Distinct from DeploymentRolloutPolicyEngine (whether a
    rollout *action* is currently allowed for a deployment, given
    conditions like health score or freeze windows) and
    GovernancePolicyEngine (governance operations like lifecycle
    transitions): neither knows who is making the request.

    Evaluation is default-deny: a principal with no roles, or roles
    granting nothing that matches, is denied. There is no explicit
    "deny" role — removing access means revoking the role that
    granted it. This engine ships with BUILT_IN_DEPLOYMENT_ROLES
    already registered (Administrator, Release Manager, Developer,
    Operator, Auditor, Security Officer, Read Only), restored by
    clear() rather than left empty, so a fresh runtime is never
    accidentally locked out of its own administration.

    Every authorize() call publishes an "authorization_granted" or
    "authorization_denied" event and records an audit entry (both
    optional, no-ops if not wired).

    Thread-safe: every mutation of the role and principal registries
    is guarded by an internal lock.
    """

    def __init__(
        self,
        *,
        clock: "Any | None" = None,
        event_bus: "GovernanceEventBus | None" = None,
        audit_service: "GovernanceAuditService | None" = None,
    ) -> None:
        self._lock = threading.Lock()

        self._roles: "dict[str, DeploymentRole]" = {}

        self._principal_roles: "dict[str, frozenset[str]]" = {}

        self._clock = clock or (
            lambda: datetime.now(timezone.utc)
        )

        self._event_bus = event_bus

        self._audit_service = audit_service

        self._register_built_in_roles()

    def register_role(
        self, name: str, permissions: "Iterable[str]"
    ) -> DeploymentRole:
        """
        Register a new named role with an immutable permission set.

        Raises ValueError if name is already registered (including a
        built-in role name).
        """

        if not name:
            raise ValueError("name must not be empty")

        with self._lock:
            if name in self._roles:
                raise ValueError(f"role '{name}' is already registered")

            role = DeploymentRole(
                name=name, permissions=frozenset(permissions)
            )

            self._roles[name] = role

        self._publish("role_registered", name, role.to_dict())

        return role

    def update_role_permissions(
        self, name: str, permissions: "Iterable[str]"
    ) -> DeploymentRole:
        """
        Replace a registered role's permission set.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._roles:
                raise KeyError(f"role '{name}' is not registered")

            role = DeploymentRole(
                name=name, permissions=frozenset(permissions)
            )

            self._roles[name] = role

            return role

    def remove_role(self, name: str) -> None:
        """
        Remove a registered role, revoking it from every principal it
        was assigned to.

        Raises KeyError if name is not registered.
        """

        with self._lock:
            if name not in self._roles:
                raise KeyError(f"role '{name}' is not registered")

            del self._roles[name]

            for principal_id, roles in list(
                self._principal_roles.items()
            ):
                if name in roles:
                    self._principal_roles[principal_id] = roles - {name}

        self._publish("role_removed", name, {})

    def assign_role(
        self, principal_id: str, role: str
    ) -> DeploymentPrincipal:
        """
        Assign role to principal_id, creating the principal if this is
        its first role. Idempotent: assigning a role the principal
        already holds is a no-op.

        Raises ValueError if principal_id is empty, or KeyError if
        role is not registered.
        """

        if not principal_id:
            raise ValueError("principal_id must not be empty")

        with self._lock:
            if role not in self._roles:
                raise KeyError(f"role '{role}' is not registered")

            current = self._principal_roles.get(principal_id, frozenset())
            updated = current | {role}
            self._principal_roles[principal_id] = updated

            principal = DeploymentPrincipal(
                principal_id=principal_id,
                roles=tuple(sorted(updated)),
            )

        self._publish(
            "role_assigned", principal_id, {"role": role}
        )

        return principal

    def revoke_role(
        self, principal_id: str, role: str
    ) -> DeploymentPrincipal:
        """
        Revoke role from principal_id. Idempotent: a no-op returning
        the principal's unchanged roles if it does not currently hold
        role.

        Raises KeyError if principal_id is not registered.
        """

        with self._lock:
            current = self._principal_roles.get(principal_id)

            if current is None:
                raise KeyError(
                    f"principal '{principal_id}' is not registered"
                )

            updated = current - {role}
            self._principal_roles[principal_id] = updated

            return DeploymentPrincipal(
                principal_id=principal_id,
                roles=tuple(sorted(updated)),
            )

    def authorize(
        self, principal_id: str, permission: str
    ) -> AuthorizationDecision:
        """
        Decide whether principal_id currently holds permission — the
        union of every permission granted by every role assigned to
        it. A principal with no role assignments (including one that
        was never assigned any role at all) is denied rather than
        raising: authorize() always returns a decision.

        Raises ValueError if principal_id or permission is empty.
        """

        if not principal_id:
            raise ValueError("principal_id must not be empty")

        if not permission:
            raise ValueError("permission must not be empty")

        with self._lock:
            role_names = self._principal_roles.get(
                principal_id, frozenset()
            )

            allowed = permission in self._effective_permissions(
                role_names
            )

            roles_snapshot = tuple(sorted(role_names))

        decision = AuthorizationDecision(
            principal_id=principal_id,
            permission=permission,
            allowed=allowed,
            roles=roles_snapshot,
            evaluated_at=self._clock(),
        )

        event_type = (
            "authorization_granted"
            if allowed
            else "authorization_denied"
        )

        self._publish(event_type, principal_id, decision.to_dict())

        if self._audit_service is not None:
            self._audit_service.record(
                action=event_type,
                actor=principal_id,
                resource=permission,
                outcome="success" if allowed else "failure",
                metadata=decision.to_dict(),
            )

        return decision

    def authorize_identity(
        self, identity: "DeploymentIdentity", permission: str
    ) -> AuthorizationDecision:
        """
        Convenience wrapper around authorize() for an already-
        authenticated DeploymentIdentity (see
        DeploymentAuthenticationManager) — authorize(identity.
        principal, permission). Not yet consulted by any governance
        service (DeploymentAuthenticationManager is deliberately
        standalone for now); this only spares a caller that already
        holds a DeploymentIdentity from extracting its principal by
        hand.
        """

        return self.authorize(identity.principal, permission)

    def permissions(self, principal_id: str) -> "frozenset[str]":
        """
        Return principal_id's effective permissions — the union of
        every permission granted by every role assigned to it. Empty
        if principal_id has no role assignments.
        """

        with self._lock:
            role_names = self._principal_roles.get(
                principal_id, frozenset()
            )

            return self._effective_permissions(role_names)

    def roles(self) -> "tuple[DeploymentRole, ...]":
        """
        Return every registered role, ordered deterministically by
        name.
        """

        with self._lock:
            roles = list(self._roles.values())

        return tuple(sorted(roles, key=lambda role: role.name))

    def principal_roles(self, principal_id: str) -> "tuple[str, ...]":
        """
        Return the names of every role currently assigned to
        principal_id, ordered deterministically. Empty if
        principal_id has no role assignments.
        """

        with self._lock:
            return tuple(
                sorted(
                    self._principal_roles.get(principal_id, frozenset())
                )
            )

    def clear(self) -> None:
        """
        Remove every role assignment and every non-built-in role,
        then restore BUILT_IN_DEPLOYMENT_ROLES — a governance runtime
        should never be left with no roles registered at all.
        """

        with self._lock:
            self._roles.clear()
            self._principal_roles.clear()
            self._register_built_in_roles()

    def _effective_permissions(
        self, role_names: "frozenset[str]"
    ) -> "frozenset[str]":
        effective: "set[str]" = set()

        for role_name in role_names:
            role = self._roles.get(role_name)

            if role is not None:
                effective.update(role.permissions)

        return frozenset(effective)

    def _register_built_in_roles(self) -> None:
        for name, permissions in BUILT_IN_DEPLOYMENT_ROLES.items():
            self._roles[name] = DeploymentRole(
                name=name, permissions=permissions
            )

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


def build_default_governance_rbac_engine() -> DeploymentRBACEngine:
    """
    Build the process-wide deployment RBAC engine, wired to the
    process-wide governance event bus and audit service.

    Also wires itself into the process-wide rollout manager, rollout
    policy engine, and rollout dashboard via their set_rbac_engine() —
    those cannot wire this engine back via constructor injection since
    they are constructed first, matching
    build_default_governance_rollout_policy_engine's own wiring of
    itself into the rollout manager/traffic router/rollback engine.
    """

    from .deployment_governance_audit import get_audit_service
    from .deployment_governance_event_bus import get_event_bus
    from .deployment_governance_rollout_dashboard import (
        get_rollout_dashboard,
    )
    from .deployment_governance_rollout_manager import (
        get_rollout_manager,
    )
    from .deployment_governance_rollout_policy import (
        get_rollout_policy_engine,
    )

    engine = DeploymentRBACEngine(
        event_bus=get_event_bus(),
        audit_service=get_audit_service(),
    )

    get_rollout_manager().set_rbac_engine(engine)
    get_rollout_policy_engine().set_rbac_engine(engine)
    get_rollout_dashboard().set_rbac_engine(engine)

    return engine


# Shared for the lifetime of the process: role and principal
# assignments registered through the API need to be enforced
# identically by every protected governance operation, which a
# persistence runtime built fresh per request cannot provide on its
# own.
_rbac_engine = build_default_governance_rbac_engine()


def get_rbac_engine() -> DeploymentRBACEngine:
    """
    Return the process-wide deployment RBAC engine.
    """

    return _rbac_engine
