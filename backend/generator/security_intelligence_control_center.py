from dataclasses import dataclass


@dataclass
class SecurityIntelligenceControlCenter:

    authentication_enabled: bool

    authorization_enabled: bool

    api_security_enabled: bool

    secret_management_enabled: bool

    vulnerability_assessment_enabled: bool

    threat_modeling_enabled: bool

    security_compliance_enabled: bool

    security_audit_enabled: bool

    security_report_enabled: bool


class SecurityIntelligenceControlCenterGenerator:

    def generate(
        self
    ):

        return (

            SecurityIntelligenceControlCenter(

                authentication_enabled=
                    True,

                authorization_enabled=
                    True,

                api_security_enabled=
                    True,

                secret_management_enabled=
                    True,

                vulnerability_assessment_enabled=
                    True,

                threat_modeling_enabled=
                    True,

                security_compliance_enabled=
                    True,

                security_audit_enabled=
                    True,

                security_report_enabled=
                    True
            )
        )
