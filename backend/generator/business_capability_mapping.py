from dataclasses import dataclass


@dataclass
class BusinessCapability:

    capability_name: str

    business_domain: str

    maturity: str


class BusinessCapabilityMappingEngine:

    def generate(
        self
    ):

        return [
            BusinessCapability(
                capability_name=
                    "Customer Management",
                business_domain=
                    "CRM",
                maturity=
                    "advanced"
            ),
            BusinessCapability(
                capability_name=
                    "Order Processing",
                business_domain=
                    "Operations",
                maturity=
                    "intermediate"
            ),
            BusinessCapability(
                capability_name=
                    "Business Analytics",
                business_domain=
                    "Reporting",
                maturity=
                    "advanced"
            )
        ]
