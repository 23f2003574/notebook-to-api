from dataclasses import dataclass


@dataclass
class ComplianceControl:

    standard: str

    control: str

    compliant: bool


@dataclass
class SecurityCompliance:

    controls: list[ComplianceControl]

    compliant_controls: int

    total_controls: int


class SecurityComplianceEngine:

    def generate(
        self
    ):

        controls = [

            ComplianceControl(

                standard=
                    "OWASP API Security",

                control=
                    "Authentication",

                compliant=
                    True
            ),

            ComplianceControl(

                standard=
                    "OWASP API Security",

                control=
                    "Authorization",

                compliant=
                    True
            ),

            ComplianceControl(

                standard=
                    "OWASP API Security",

                control=
                    "Rate Limiting",

                compliant=
                    True
            )
        ]

        return SecurityCompliance(

            controls=
                controls,

            compliant_controls=
                len(
                    controls
                ),

            total_controls=
                len(
                    controls
                )
        )
