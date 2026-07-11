from dataclasses import dataclass


@dataclass
class RecoveryVerification:

    incident_id: str

    expected_state: str

    observed_state: str

    recovered: bool


class RecoveryVerificationEngine:

    def verify(
        self,
        incident_id: str,
        expected_state: str,
        observed_state: str
    ):

        return RecoveryVerification(

            incident_id=
                incident_id,

            expected_state=
                expected_state,

            observed_state=
                observed_state,

            recovered=
                expected_state == observed_state
        )
