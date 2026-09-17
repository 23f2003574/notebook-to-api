from dataclasses import dataclass


@dataclass
class LoadBalancedEndpoint:

    service_name: str

    endpoint: str

    strategy: str


class LoadBalancingEngine:

    def resolve(
        self,
        service_name: str
    ):

        return LoadBalancedEndpoint(

            service_name=
                service_name,

            endpoint=
                "http://localhost:8000",

            strategy=
                "round_robin"
        )
