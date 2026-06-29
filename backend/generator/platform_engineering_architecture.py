from dataclasses import dataclass


@dataclass
class PlatformEngineeringArchitecture:

    architecture_style: str

    platform_services: list[str]

    service_catalog_enabled: bool

    platform_api_model: str


class PlatformEngineeringArchitectureEngine:

    def generate(
        self
    ):

        return PlatformEngineeringArchitecture(

            architecture_style=
                "platform_as_a_product",

            platform_services=[
                "developer_portal",
                "software_catalog",
                "ci_cd_platform",
                "observability_platform",
                "secrets_management"
            ],

            service_catalog_enabled=
                True,

            platform_api_model=
                "self_service"
        )
