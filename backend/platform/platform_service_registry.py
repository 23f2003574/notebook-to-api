from dataclasses import dataclass


@dataclass
class PlatformService:

    service_name: str

    service_type: str

    version: str

    available: bool


class PlatformServiceRegistry:

    def discover(
        self,
        service_name: str
    ):

        return PlatformService(

            service_name=
                service_name,

            service_type=
                "internal",

            version=
                "1.0.0",

            available=
                True
        )
