from dataclasses import dataclass
from datetime import datetime


@dataclass
class AuditRecord:

    actor: str

    action: str

    resource: str

    timestamp: datetime

    successful: bool


class PlatformAuditEngine:

    def record(
        self,
        actor: str,
        action: str,
        resource: str,
        successful: bool
    ):

        return AuditRecord(

            actor=actor,

            action=action,

            resource=resource,

            timestamp=datetime.utcnow(),

            successful=successful
        )
