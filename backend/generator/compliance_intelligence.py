from dataclasses import dataclass


@dataclass
class ComplianceFramework:

    framework_name: str

    compliance_status: str

    coverage_percent: float


class ComplianceIntelligenceEngine:

    def generate(
        self
    ):

        return [

            ComplianceFramework(

                framework_name=
                    "SOC2",

                compliance_status=
                    "partial",

                coverage_percent=
                    82.0
            ),

            ComplianceFramework(

                framework_name=
                    "ISO27001",

                compliance_status=
                    "partial",

                coverage_percent=
                    78.0
            ),

            ComplianceFramework(

                framework_name=
                    "GDPR",

                compliance_status=
                    "compliant",

                coverage_percent=
                    94.0
            )
        ]
