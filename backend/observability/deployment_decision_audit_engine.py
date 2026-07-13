from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List
from uuid import uuid4


@dataclass
class DeploymentDecisionAuditRecord:

    audit_id: str

    service_name: str

    environment: str

    decision: str

    matched_rules: List[str]

    reasons: List[str]

    created_at: str


@dataclass
class AuditedDeploymentDecision:

    decision: object

    audit_record: DeploymentDecisionAuditRecord


class DeploymentDecisionAuditEngine:

    def record(
        self,
        service_name: str,
        environment: str,
        decision: str,
        matched_rules: List[str],
        reasons: List[str]
    ):

        return DeploymentDecisionAuditRecord(

            audit_id=
                str(uuid4()),

            service_name=
                service_name,

            environment=
                environment,

            decision=
                decision,

            matched_rules=
                list(matched_rules),

            reasons=
                list(reasons),

            created_at=
                datetime
                .now(timezone.utc)
                .isoformat()
        )
