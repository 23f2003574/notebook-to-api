from dataclasses import dataclass


@dataclass
class ThreatScenario:

    threat: str

    severity: str

    mitigation: str


@dataclass
class ThreatModel:

    scenarios: list[ThreatScenario]

    scenario_count: int


class ThreatModelingEngine:

    def generate(
        self
    ):

        scenarios = [

            ThreatScenario(

                threat=
                    "credential_theft",

                severity=
                    "high",

                mitigation=
                    "token_rotation"
            ),

            ThreatScenario(

                threat=
                    "rate_limit_abuse",

                severity=
                    "medium",

                mitigation=
                    "request_throttling"
            ),

            ThreatScenario(

                threat=
                    "unauthorized_access",

                severity=
                    "high",

                mitigation=
                    "rbac_enforcement"
            )
        ]

        return ThreatModel(

            scenarios=
                scenarios,

            scenario_count=
                len(
                    scenarios
                )
        )
