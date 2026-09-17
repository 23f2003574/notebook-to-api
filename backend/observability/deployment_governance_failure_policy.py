from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Protocol, runtime_checkable


class GovernanceIntegrityFailureAction(
    str,
    Enum,
):
    """
    What should happen to a governance audit execution once its
    retry budget is exhausted.
    """

    IGNORE = "ignore"

    RETRY = "retry"

    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class GovernanceIntegrityFailurePolicy:
    """
    A named failure-handling policy: how many times a failed
    execution may be retried before falling back to a configured
    action.
    """

    name: str

    action: GovernanceIntegrityFailureAction

    max_retry_attempts: int

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError(
                "name must not be empty"
            )

        if self.max_retry_attempts < 0:
            raise ValueError(
                "max_retry_attempts must be greater than or equal "
                "to zero"
            )

    def resolve(
        self,
        retry_attempts: int,
    ) -> GovernanceIntegrityFailureAction:
        """
        Determine what should happen next for an execution that has
        already been retried retry_attempts times.

        Returns RETRY while the retry budget is not yet exhausted,
        and the configured action once it is.
        """

        if retry_attempts < self.max_retry_attempts:
            return GovernanceIntegrityFailureAction.RETRY

        return self.action

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "action": self.action.value,
            "max_retry_attempts": self.max_retry_attempts,
        }


@runtime_checkable
class GovernanceIntegrityFailurePolicyRepository(Protocol):
    """
    Persistence contract for named governance audit failure policies.
    """

    def save(
        self,
        policy: GovernanceIntegrityFailurePolicy,
    ) -> GovernanceIntegrityFailurePolicy:
        """
        Persist one policy, replacing any existing policy with the
        same name.
        """

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityFailurePolicy | None:
        """
        Return one policy by name, or None if it does not exist.
        """

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityFailurePolicy,
        ...
    ]:
        """
        Return every policy, ordered by name.
        """

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete one policy by name. Raises KeyError if it does not
        exist.
        """

    def exists(
        self,
        name: str,
    ) -> bool:
        """
        Return whether a policy with this name exists.
        """


class InMemoryGovernanceIntegrityFailurePolicyRepository:
    """
    Thread-safe in-memory implementation of governance audit failure
    policy storage.
    """

    def __init__(self) -> None:
        self._policies: dict[
            str,
            GovernanceIntegrityFailurePolicy,
        ] = {}

        self._lock = RLock()

    def save(
        self,
        policy: GovernanceIntegrityFailurePolicy,
    ) -> GovernanceIntegrityFailurePolicy:
        with self._lock:
            self._policies[policy.name] = policy

            return policy

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityFailurePolicy | None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return self._policies.get(normalized_name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityFailurePolicy,
        ...
    ]:
        with self._lock:
            return tuple(
                sorted(
                    self._policies.values(),
                    key=lambda policy: policy.name,
                )
            )

    def delete(
        self,
        name: str,
    ) -> None:
        normalized_name = self._normalize_name(name)

        with self._lock:
            if normalized_name not in self._policies:
                raise KeyError(
                    f"failure policy '{normalized_name}' was not found"
                )

            del self._policies[normalized_name]

    def exists(
        self,
        name: str,
    ) -> bool:
        normalized_name = self._normalize_name(name)

        with self._lock:
            return normalized_name in self._policies

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized_name = name.strip()

        if not normalized_name:
            raise ValueError(
                "name must not be empty"
            )

        return normalized_name


class GovernanceIntegrityFailurePolicyService:
    """
    Creates and manages named governance audit failure policies.
    """

    def __init__(
        self,
        repository: GovernanceIntegrityFailurePolicyRepository,
    ) -> None:
        self._repository = repository

    def create(
        self,
        name: str,
        action: GovernanceIntegrityFailureAction,
        max_retry_attempts: int,
    ) -> GovernanceIntegrityFailurePolicy:
        """
        Create a new, uniquely named failure policy.

        Raises ValueError if a policy with this name already exists.
        """

        if self._repository.exists(name):
            raise ValueError(
                f"failure policy '{name}' already exists"
            )

        policy = GovernanceIntegrityFailurePolicy(
            name=name,
            action=action,
            max_retry_attempts=max_retry_attempts,
        )

        return self._repository.save(policy)

    def update(
        self,
        name: str,
        *,
        action: GovernanceIntegrityFailureAction | None = None,
        max_retry_attempts: int | None = None,
    ) -> GovernanceIntegrityFailurePolicy:
        """
        Update an existing policy's action and/or retry budget.

        Fields left as None keep their current value. Raises
        LookupError if no policy with this name exists.
        """

        existing = self._repository.get(name)

        if existing is None:
            raise LookupError(
                f"failure policy '{name}' was not found"
            )

        updated = GovernanceIntegrityFailurePolicy(
            name=existing.name,
            action=(
                existing.action
                if action is None
                else action
            ),
            max_retry_attempts=(
                existing.max_retry_attempts
                if max_retry_attempts is None
                else max_retry_attempts
            ),
        )

        return self._repository.save(updated)

    def delete(
        self,
        name: str,
    ) -> None:
        """
        Delete a policy by name. Raises KeyError if it does not
        exist.
        """

        self._repository.delete(name)

    def get(
        self,
        name: str,
    ) -> GovernanceIntegrityFailurePolicy | None:
        return self._repository.get(name)

    def list(
        self,
    ) -> tuple[
        GovernanceIntegrityFailurePolicy,
        ...
    ]:
        return self._repository.list()
