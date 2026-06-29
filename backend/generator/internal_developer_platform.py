from dataclasses import dataclass


@dataclass
class InternalDeveloperPlatform:

    platform_type: str

    developer_portal: str

    self_service_model: str

    software_catalog_enabled: bool


class InternalDeveloperPlatformEngine:

    def generate(
        self
    ):

        return InternalDeveloperPlatform(

            platform_type=
                "internal_developer_platform",

            developer_portal=
                "Backstage",

            self_service_model=
                "golden_paths",

            software_catalog_enabled=
                True
        )
