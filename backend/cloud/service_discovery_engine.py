from dataclasses import dataclass


@dataclass
class ServiceEndpoint:

    service_name: str

    host: str

    port: int

    healthy: bool


class ServiceDiscoveryEngine:

    def discover(
        self,
        service_name: str
    ):

        return ServiceEndpoint(

            service_name=
                service_name,

            host=
                "localhost",

            port=
                8000,

            healthy=
                True
        )
