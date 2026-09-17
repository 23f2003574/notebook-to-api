from dataclasses import dataclass


@dataclass
class EnterpriseArchitecture:

    architecture_style: str

    integration_pattern: str

    bounded_context: str

    deployment_domain: str


class EnterpriseArchitectureEngine:

    def generate(
        self
    ):

        return EnterpriseArchitecture(
            architecture_style=
                "event_driven_microservices",
            integration_pattern=
                "api_gateway",
            bounded_context=
                "customer_services",
            deployment_domain=
                "enterprise_platform"
        )
