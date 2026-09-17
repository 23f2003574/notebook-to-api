from dataclasses import dataclass


@dataclass
class PolicyControl:

    policy_name: str

    enforcement_status: str

    severity: str


class PolicyEnforcementEngine:

    def generate(
        self
    ):

        return [

            PolicyControl(

                policy_name=
                    "authentication_required",

                enforcement_status=
                    "enforced",

                severity=
                    "critical"
            ),

            PolicyControl(

                policy_name=
                    "encryption_required",

                enforcement_status=
                    "enforced",

                severity=
                    "critical"
            ),

            PolicyControl(

                policy_name=
                    "audit_logging_required",

                enforcement_status=
                    "partial",

                severity=
                    "high"
            )
        ]
