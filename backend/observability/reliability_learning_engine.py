from dataclasses import dataclass


@dataclass
class ReliabilityLearningRecord:

    incident_id: str

    root_cause: str

    remediation_action: str

    recovery_successful: bool


class ReliabilityLearningEngine:

    def learn(
        self,
        incident_id: str,
        root_cause: str,
        remediation_action: str,
        recovery_successful: bool
    ):

        return ReliabilityLearningRecord(

            incident_id=
                incident_id,

            root_cause=
                root_cause,

            remediation_action=
                remediation_action,

            recovery_successful=
                recovery_successful
        )
