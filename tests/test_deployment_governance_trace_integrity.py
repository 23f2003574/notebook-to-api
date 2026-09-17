from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from backend.observability.deployment_governance_trace_integrity import (
    DeploymentGovernanceTraceIntegrity,
    GovernanceTraceIntegrityMismatchError,
)
from backend.observability.deployment_governance_trace_repository import (
    GovernanceTraceRecord,
)


def make_record() -> GovernanceTraceRecord:
    timestamp = datetime(
        2026,
        7,
        14,
        12,
        0,
        0,
        tzinfo=timezone.utc,
    )

    return GovernanceTraceRecord(
        trace_id="trace-integrity",
        deployment_id="deployment-integrity",
        service_name="payments-api",
        environment="production",
        artifact_digest="sha256:artifact",
        created_at=timestamp,
        updated_at=timestamp,
        governance_state="created",
        final_status=None,
        completed=False,
        payload={
            "schema_version": 1,
            "trace": {
                "trace_id": "trace-integrity",
                "deployment_id": (
                    "deployment-integrity"
                ),
            },
            "events": [],
        },
    )


def test_same_record_produces_same_integrity_digest() -> None:
    record = make_record()

    first = (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        )
    )

    second = (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        )
    )

    assert first == second


def test_mapping_key_order_does_not_change_digest() -> None:
    record = make_record()

    reordered = replace(
        record,
        payload={
            "events": [],
            "trace": {
                "deployment_id": (
                    "deployment-integrity"
                ),
                "trace_id": (
                    "trace-integrity"
                ),
            },
            "schema_version": 1,
        },
    )

    assert (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        )
        ==
        DeploymentGovernanceTraceIntegrity.calculate(
            reordered
        )
    )


def test_metadata_change_changes_integrity_digest() -> None:
    record = make_record()

    modified = replace(
        record,
        environment="staging",
    )

    assert (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        ).digest
        !=
        DeploymentGovernanceTraceIntegrity.calculate(
            modified
        ).digest
    )


def test_payload_change_changes_integrity_digest() -> None:
    record = make_record()

    modified = replace(
        record,
        payload={
            **record.payload,
            "events": [
                {
                    "type": "approval_requested",
                }
            ],
        },
    )

    assert (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        ).digest
        !=
        DeploymentGovernanceTraceIntegrity.calculate(
            modified
        ).digest
    )


def test_valid_record_passes_integrity_verification() -> None:
    record = make_record()

    metadata = (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        )
    )

    DeploymentGovernanceTraceIntegrity.verify(
        record,
        metadata,
    )


def test_modified_record_fails_integrity_verification() -> None:
    record = make_record()

    metadata = (
        DeploymentGovernanceTraceIntegrity.calculate(
            record
        )
    )

    modified = replace(
        record,
        governance_state="succeeded",
        final_status="succeeded",
        completed=True,
    )

    with pytest.raises(
        GovernanceTraceIntegrityMismatchError
    ):
        DeploymentGovernanceTraceIntegrity.verify(
            modified,
            metadata,
        )
