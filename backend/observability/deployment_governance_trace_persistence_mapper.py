from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from .deployment_governance_state_projector import (
    DeploymentGovernanceStateProjector,
)
from .deployment_governance_trace_engine import (
    DeploymentGovernanceTrace,
    DeploymentGovernanceTraceEvent,
)
from .deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)


class GovernanceTracePersistenceError(ValueError):
    """
    Raised when a governance trace cannot be safely converted to or restored
    from its persistence representation.
    """


class UnsupportedGovernanceTracePayloadVersionError(
    GovernanceTracePersistenceError
):
    """
    Raised when persisted trace data uses an unsupported payload version.
    """


class DeploymentGovernanceTracePersistenceMapper:
    """
    Canonical translation boundary between deployment governance domain traces
    and storage-neutral GovernanceTraceRecord instances.

    Repositories should persist GovernanceTraceRecord values. Domain and
    application services should continue operating on DeploymentGovernanceTrace
    values.

    This mapper prevents serialization, semantic-state derivation, and recovery
    logic from being duplicated across callers.

    Note: DeploymentGovernanceTrace and DeploymentGovernanceTraceEvent store
    their timestamps as ISO 8601 strings rather than datetime objects, so this
    mapper coerces between the two representations at the persistence boundary.
    """

    PAYLOAD_SCHEMA_VERSION = 1

    TERMINAL_STATUSES = frozenset(
        {
            "succeeded",
            "failed",
            "blocked",
            "rejected",
        }
    )

    def __init__(
        self,
        state_projector: DeploymentGovernanceStateProjector | None = None,
    ) -> None:
        self._state_projector = (
            state_projector
            or DeploymentGovernanceStateProjector()
        )

    def to_record(
        self,
        trace: DeploymentGovernanceTrace,
    ) -> GovernanceTraceRecord:
        """
        Convert a domain governance trace into its canonical persistence
        representation.
        """

        self._validate_trace(trace)

        projection = self._state_projector.project(trace)

        events = self._get_trace_events(trace)

        created_at = self._coerce_datetime(
            self._require_attribute(
                trace,
                "created_at",
            )
        )

        updated_at = self._derive_updated_at(
            trace=trace,
            events=events,
            created_at=created_at,
        )

        governance_state = self._projection_value(
            projection,
            "state",
            default="created",
        )

        terminal = bool(
            self._projection_value(
                projection,
                "terminal",
                default=False,
            )
        )

        final_status = self._derive_final_status(
            projection=projection,
            governance_state=governance_state,
            terminal=terminal,
        )

        payload = self._trace_to_payload(
            trace=trace,
            events=events,
        )

        return GovernanceTraceRecord(
            trace_id=str(
                self._require_attribute(
                    trace,
                    "trace_id",
                )
            ),
            deployment_id=str(
                self._require_attribute(
                    trace,
                    "deployment_id",
                )
            ),
            service_name=str(
                self._require_attribute(
                    trace,
                    "service_name",
                )
            ),
            environment=str(
                self._require_attribute(
                    trace,
                    "environment",
                )
            ),
            artifact_digest=str(
                self._require_attribute(
                    trace,
                    "artifact_digest",
                )
            ),
            created_at=created_at,
            updated_at=updated_at,
            governance_state=str(governance_state),
            final_status=final_status,
            completed=terminal,
            payload=payload,
        )

    def from_record(
        self,
        record: GovernanceTraceRecord,
    ) -> DeploymentGovernanceTrace:
        """
        Restore a domain governance trace from a persisted record.
        """

        self._validate_record(record)

        payload = deepcopy(dict(record.payload))

        version = payload.get(
            "schema_version",
            self.PAYLOAD_SCHEMA_VERSION,
        )

        if version != self.PAYLOAD_SCHEMA_VERSION:
            raise UnsupportedGovernanceTracePayloadVersionError(
                "unsupported deployment governance trace payload version "
                f"'{version}'"
            )

        trace_payload = payload.get("trace")

        if not isinstance(trace_payload, Mapping):
            raise GovernanceTracePersistenceError(
                "persisted governance trace payload is missing "
                "a valid 'trace' object"
            )

        events_payload = payload.get(
            "events",
            (),
        )

        if not isinstance(
            events_payload,
            (list, tuple),
        ):
            raise GovernanceTracePersistenceError(
                "persisted governance trace 'events' must be a sequence"
            )

        trace = self._payload_to_trace(
            record=record,
            trace_payload=trace_payload,
            events_payload=events_payload,
        )

        self._validate_restored_identity(
            trace=trace,
            record=record,
        )

        return trace

    def to_records(
        self,
        traces: Sequence[DeploymentGovernanceTrace],
    ) -> tuple[GovernanceTraceRecord, ...]:
        """
        Convert multiple domain traces into persistence records.
        """

        return tuple(
            self.to_record(trace)
            for trace in traces
        )

    def from_records(
        self,
        records: Sequence[GovernanceTraceRecord],
    ) -> tuple[DeploymentGovernanceTrace, ...]:
        """
        Restore multiple domain traces from persistence records.
        """

        return tuple(
            self.from_record(record)
            for record in records
        )

    def _trace_to_payload(
        self,
        *,
        trace: DeploymentGovernanceTrace,
        events: Sequence[Any],
    ) -> dict[str, Any]:
        """
        Build the canonical versioned persistence payload.
        """

        trace_payload = self._serialize_dataclass_or_object(
            trace,
            excluded_fields={"events"},
        )

        return {
            "schema_version": self.PAYLOAD_SCHEMA_VERSION,
            "trace": trace_payload,
            "events": [
                self._event_to_payload(event)
                for event in events
            ],
        }

    def _event_to_payload(
        self,
        event: Any,
    ) -> dict[str, Any]:
        """
        Serialize one governance trace event into JSON-compatible primitives.
        """

        serialized = self._serialize_dataclass_or_object(
            event
        )

        return self._to_primitive(serialized)

    def _payload_to_trace(
        self,
        *,
        record: GovernanceTraceRecord,
        trace_payload: Mapping[str, Any],
        events_payload: Sequence[Mapping[str, Any]],
    ) -> DeploymentGovernanceTrace:
        """
        Reconstruct a DeploymentGovernanceTrace.

        DeploymentGovernanceTrace stores created_at as an ISO 8601 string, so
        the repository's indexed (datetime) created_at is converted back to
        that string form rather than injected as a datetime.
        """

        trace_kwargs = dict(trace_payload)

        trace_kwargs.update(
            {
                "trace_id": record.trace_id,
                "deployment_id": record.deployment_id,
                "service_name": record.service_name,
                "environment": record.environment,
                "artifact_digest": record.artifact_digest,
                "created_at": record.created_at.isoformat(),
            }
        )

        events = [
            self._payload_to_event(event_payload)
            for event_payload in events_payload
        ]

        trace_kwargs["events"] = events

        trace_kwargs = self._filter_constructor_arguments(
            DeploymentGovernanceTrace,
            trace_kwargs,
        )

        try:
            return DeploymentGovernanceTrace(
                **trace_kwargs
            )
        except TypeError as exc:
            raise GovernanceTracePersistenceError(
                "failed to reconstruct DeploymentGovernanceTrace from "
                "persisted payload"
            ) from exc

    def _payload_to_event(
        self,
        payload: Mapping[str, Any],
    ) -> DeploymentGovernanceTraceEvent:
        """
        Reconstruct one governance trace event.

        DeploymentGovernanceTraceEvent.timestamp is an ISO 8601 string field,
        so the persisted string value is passed through unchanged rather than
        parsed into a datetime.
        """

        event_kwargs = dict(payload)

        event_kwargs = self._filter_constructor_arguments(
            DeploymentGovernanceTraceEvent,
            event_kwargs,
        )

        try:
            return DeploymentGovernanceTraceEvent(
                **event_kwargs
            )
        except TypeError as exc:
            raise GovernanceTracePersistenceError(
                "failed to reconstruct DeploymentGovernanceTraceEvent "
                "from persisted payload"
            ) from exc

    def _derive_updated_at(
        self,
        *,
        trace: DeploymentGovernanceTrace,
        events: Sequence[Any],
        created_at: datetime,
    ) -> datetime:
        explicit_updated_at = getattr(
            trace,
            "updated_at",
            None,
        )

        if isinstance(
            explicit_updated_at,
            datetime,
        ):
            return self._normalize_datetime(
                explicit_updated_at
            )

        if isinstance(explicit_updated_at, str) and explicit_updated_at:
            return self._parse_datetime(explicit_updated_at)

        event_timestamps = [
            timestamp
            for event in events
            if (
                timestamp
                := self._extract_event_timestamp(
                    event
                )
            )
            is not None
        ]

        if not event_timestamps:
            return created_at

        return max(event_timestamps)

    def _derive_final_status(
        self,
        *,
        projection: Any,
        governance_state: str,
        terminal: bool,
    ) -> str | None:
        explicit_final_status = self._projection_value(
            projection,
            "final_status",
            default=None,
        )

        if explicit_final_status is not None:
            return str(explicit_final_status)

        if (
            terminal
            and governance_state
            in self.TERMINAL_STATUSES
        ):
            return governance_state

        return None

    @staticmethod
    def _projection_value(
        projection: Any,
        attribute: str,
        *,
        default: Any,
    ) -> Any:
        if isinstance(
            projection,
            Mapping,
        ):
            return projection.get(
                attribute,
                default,
            )

        return getattr(
            projection,
            attribute,
            default,
        )

    @staticmethod
    def _get_trace_events(
        trace: DeploymentGovernanceTrace,
    ) -> tuple[Any, ...]:
        events = getattr(
            trace,
            "events",
            (),
        )

        if events is None:
            return ()

        return tuple(events)

    def _validate_trace(
        self,
        trace: DeploymentGovernanceTrace,
    ) -> None:
        for attribute in (
            "trace_id",
            "deployment_id",
            "service_name",
            "environment",
            "artifact_digest",
            "created_at",
        ):
            self._require_attribute(
                trace,
                attribute,
            )

    @staticmethod
    def _validate_record(
        record: GovernanceTraceRecord,
    ) -> None:
        if not isinstance(
            record.payload,
            Mapping,
        ):
            raise GovernanceTracePersistenceError(
                "governance trace record payload must be a mapping"
            )

    def _validate_restored_identity(
        self,
        *,
        trace: DeploymentGovernanceTrace,
        record: GovernanceTraceRecord,
    ) -> None:
        identity_fields = (
            "trace_id",
            "deployment_id",
            "service_name",
            "environment",
            "artifact_digest",
        )

        for field_name in identity_fields:
            trace_value = str(
                self._require_attribute(
                    trace,
                    field_name,
                )
            )

            record_value = str(
                getattr(
                    record,
                    field_name,
                )
            )

            if trace_value != record_value:
                raise GovernanceTracePersistenceError(
                    "restored governance trace identity mismatch for "
                    f"'{field_name}'"
                )

    @staticmethod
    def _require_attribute(
        value: Any,
        attribute: str,
    ) -> Any:
        if not hasattr(
            value,
            attribute,
        ):
            raise GovernanceTracePersistenceError(
                "governance trace is missing required attribute "
                f"'{attribute}'"
            )

        result = getattr(
            value,
            attribute,
        )

        if result is None:
            raise GovernanceTracePersistenceError(
                "governance trace attribute "
                f"'{attribute}' cannot be None"
            )

        return result

    def _serialize_dataclass_or_object(
        self,
        value: Any,
        excluded_fields: set[str] | None = None,
    ) -> dict[str, Any]:
        excluded_fields = (
            excluded_fields
            or set()
        )

        if is_dataclass(value):
            raw = asdict(value)
        elif hasattr(
            value,
            "__dict__",
        ):
            raw = dict(
                vars(value)
            )
        else:
            raise GovernanceTracePersistenceError(
                "governance persistence mapper cannot serialize object "
                f"of type '{type(value).__name__}'"
            )

        return {
            key: self._to_primitive(item)
            for key, item in raw.items()
            if (
                key not in excluded_fields
                and not key.startswith("_")
            )
        }

    def _to_primitive(
        self,
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if isinstance(
            value,
            Enum,
        ):
            return value.value

        if isinstance(
            value,
            datetime,
        ):
            return self._normalize_datetime(
                value
            ).isoformat()

        if is_dataclass(value):
            return {
                key: self._to_primitive(item)
                for key, item in asdict(value).items()
            }

        if isinstance(
            value,
            Mapping,
        ):
            return {
                str(key): self._to_primitive(item)
                for key, item in value.items()
            }

        if isinstance(
            value,
            (list, tuple, set, frozenset),
        ):
            return [
                self._to_primitive(item)
                for item in value
            ]

        if isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            ),
        ):
            return value

        return str(value)

    @staticmethod
    def _filter_constructor_arguments(
        target_type: type,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """
        Restrict persisted values to declared dataclass fields when the target
        is a dataclass.

        This gives versioned payloads room to contain additional metadata
        without automatically breaking older constructors.
        """

        if not is_dataclass(
            target_type
        ):
            return dict(values)

        allowed_fields = {
            field.name
            for field in fields(
                target_type
            )
            if field.init
        }

        return {
            key: value
            for key, value in values.items()
            if key in allowed_fields
        }

    def _extract_event_timestamp(
        self,
        event: Any,
    ) -> datetime | None:
        for attribute in (
            "timestamp",
            "created_at",
            "occurred_at",
        ):
            value = getattr(
                event,
                attribute,
                None,
            )

            if isinstance(
                value,
                datetime,
            ):
                return self._normalize_datetime(
                    value
                )

            if isinstance(value, str) and value:
                try:
                    return self._parse_datetime(value)
                except GovernanceTracePersistenceError:
                    continue

        return None

    def _coerce_datetime(
        self,
        value: Any,
    ) -> datetime:
        if isinstance(value, datetime):
            return self._normalize_datetime(value)

        if isinstance(value, str):
            return self._parse_datetime(value)

        raise GovernanceTracePersistenceError(
            f"cannot interpret '{value!r}' as a governance trace timestamp"
        )

    @staticmethod
    def _normalize_datetime(
        value: datetime,
    ) -> datetime:
        if value.tzinfo is None:
            return value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            timezone.utc
        )

    @staticmethod
    def _parse_datetime(
        value: str,
    ) -> datetime:
        try:
            parsed = datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            )
        except ValueError as exc:
            raise GovernanceTracePersistenceError(
                f"invalid persisted datetime value '{value}'"
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )
