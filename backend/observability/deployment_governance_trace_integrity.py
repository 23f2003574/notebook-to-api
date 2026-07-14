from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Mapping

from backend.persistence.sqlite_database import SQLitePersistenceError

from .deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)


GOVERNANCE_TRACE_INTEGRITY_ALGORITHM: Final[
    str
] = "sha256"

GOVERNANCE_TRACE_INTEGRITY_VERSION: Final[
    int
] = 1


class GovernanceTraceIntegrityError(SQLitePersistenceError):
    """
    Base error for deployment governance persistence integrity failures.
    """


class GovernanceTraceIntegrityMismatchError(
    GovernanceTraceIntegrityError
):
    """
    Raised when persisted governance trace content does not match its
    recorded integrity digest.
    """


class GovernanceTraceIntegrityMetadataMissingError(
    GovernanceTraceIntegrityError
):
    """
    Raised when a persisted governance trace has no integrity metadata.
    """


class GovernanceTraceIntegritySerializationError(
    GovernanceTraceIntegrityError
):
    """
    Raised when a governance trace cannot be canonicalized for integrity
    calculation.
    """


@dataclass(
    frozen=True
)
class GovernanceTraceIntegrityMetadata:
    """
    Integrity metadata associated with one persisted governance trace.
    """

    algorithm: str

    version: int

    digest: str

    def __post_init__(
        self,
    ) -> None:
        normalized_algorithm = (
            self.algorithm
            .strip()
            .lower()
        )

        object.__setattr__(
            self,
            "algorithm",
            normalized_algorithm,
        )

        if (
            normalized_algorithm
            != GOVERNANCE_TRACE_INTEGRITY_ALGORITHM
        ):
            raise ValueError(
                "unsupported governance trace integrity "
                f"algorithm '{self.algorithm}'"
            )

        if (
            self.version
            != GOVERNANCE_TRACE_INTEGRITY_VERSION
        ):
            raise ValueError(
                "unsupported governance trace integrity "
                f"version '{self.version}'"
            )

        normalized_digest = (
            self.digest
            .strip()
            .lower()
        )

        if len(
            normalized_digest
        ) != 64:
            raise ValueError(
                "SHA-256 governance trace integrity digest "
                "must contain exactly 64 hexadecimal characters"
            )

        try:
            int(
                normalized_digest,
                16,
            )

        except ValueError as exc:
            raise ValueError(
                "governance trace integrity digest "
                "must be hexadecimal"
            ) from exc

        object.__setattr__(
            self,
            "digest",
            normalized_digest,
        )


class DeploymentGovernanceTraceIntegrity:
    """
    Canonical integrity calculator and verifier for persisted governance
    trace records.

    Version 1 hashes the complete storage-neutral repository record using
    deterministic JSON serialization and SHA-256.
    """

    algorithm: Final[
        str
    ] = GOVERNANCE_TRACE_INTEGRITY_ALGORITHM

    version: Final[
        int
    ] = GOVERNANCE_TRACE_INTEGRITY_VERSION

    @classmethod
    def calculate(
        cls,
        record: GovernanceTraceRecord,
    ) -> GovernanceTraceIntegrityMetadata:
        """
        Calculate deterministic integrity metadata for a governance record.
        """

        canonical_bytes = (
            cls.canonical_bytes(
                record
            )
        )

        digest = hashlib.sha256(
            canonical_bytes
        ).hexdigest()

        return GovernanceTraceIntegrityMetadata(
            algorithm=cls.algorithm,
            version=cls.version,
            digest=digest,
        )

    @classmethod
    def verify(
        cls,
        record: GovernanceTraceRecord,
        metadata: GovernanceTraceIntegrityMetadata,
    ) -> None:
        """
        Verify that a governance record matches persisted integrity metadata.

        Raises GovernanceTraceIntegrityMismatchError when verification fails.
        """

        if metadata.algorithm != cls.algorithm:
            raise GovernanceTraceIntegrityMismatchError(
                "persisted deployment governance trace "
                "uses an unsupported integrity algorithm"
            )

        if metadata.version != cls.version:
            raise GovernanceTraceIntegrityMismatchError(
                "persisted deployment governance trace "
                "uses an unsupported integrity version"
            )

        expected = cls.calculate(
            record
        )

        if not hmac.compare_digest(
            expected.digest,
            metadata.digest,
        ):
            raise GovernanceTraceIntegrityMismatchError(
                "persisted deployment governance trace "
                f"'{record.trace_id}' failed integrity verification"
            )

    @classmethod
    def is_valid(
        cls,
        record: GovernanceTraceRecord,
        metadata: GovernanceTraceIntegrityMetadata,
    ) -> bool:
        """
        Return whether a governance record passes integrity verification.
        """

        try:
            cls.verify(
                record,
                metadata,
            )

        except GovernanceTraceIntegrityError:
            return False

        return True

    @classmethod
    def canonical_bytes(
        cls,
        record: GovernanceTraceRecord,
    ) -> bytes:
        """
        Return the canonical UTF-8 representation used for integrity hashing.
        """

        document = cls.canonical_document(
            record
        )

        try:
            serialized = json.dumps(
                document,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":",
                ),
                sort_keys=True,
                allow_nan=False,
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise GovernanceTraceIntegritySerializationError(
                "deployment governance trace cannot be "
                "canonicalized for integrity calculation"
            ) from exc

        return serialized.encode(
            "utf-8"
        )

    @classmethod
    def canonical_document(
        cls,
        record: GovernanceTraceRecord,
    ) -> dict[str, Any]:
        """
        Build the versioned canonical integrity document.

        The integrity version is embedded into the document itself so future
        algorithms can evolve without silently changing version-1 semantics.
        """

        return {
            "integrity_version": cls.version,
            "trace": {
                "trace_id": record.trace_id,
                "deployment_id": record.deployment_id,
                "service_name": record.service_name,
                "environment": record.environment,
                "artifact_digest": record.artifact_digest,
                "created_at": cls._canonical_datetime(
                    record.created_at
                ),
                "updated_at": cls._canonical_datetime(
                    record.updated_at
                ),
                "governance_state": record.governance_state,
                "final_status": record.final_status,
                "completed": record.completed,
                "payload": cls._canonicalize_value(
                    record.payload
                ),
            },
        }

    @classmethod
    def _canonicalize_value(
        cls,
        value: Any,
    ) -> Any:
        """
        Recursively normalize JSON-compatible values.

        Mapping keys are normalized to strings and mappings are reconstructed
        in deterministic key order before final JSON serialization.
        """

        if value is None:
            return None

        if isinstance(
            value,
            bool,
        ):
            return value

        if isinstance(
            value,
            (
                str,
                int,
                float,
            ),
        ):
            return value

        if isinstance(
            value,
            datetime,
        ):
            return cls._canonical_datetime(
                value
            )

        if isinstance(
            value,
            Mapping,
        ):
            normalized_items = {
                str(
                    key
                ): cls._canonicalize_value(
                    item
                )
                for key, item in value.items()
            }

            return {
                key: normalized_items[
                    key
                ]
                for key in sorted(
                    normalized_items
                )
            }

        if isinstance(
            value,
            (
                list,
                tuple,
            ),
        ):
            return [
                cls._canonicalize_value(
                    item
                )
                for item in value
            ]

        raise GovernanceTraceIntegritySerializationError(
            "deployment governance trace contains "
            "a value unsupported by integrity canonicalization: "
            f"{type(value).__name__}"
        )

    @staticmethod
    def _canonical_datetime(
        value: datetime,
    ) -> str:
        """
        Normalize datetimes to canonical UTC ISO 8601 representation.
        """

        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )
        else:
            value = value.astimezone(
                timezone.utc
            )

        return value.isoformat()
