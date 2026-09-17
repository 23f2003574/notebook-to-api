from dataclasses import dataclass


@dataclass
class VirtualNetwork:

    network_id: str

    name: str

    cidr: str

    isolated: bool


class InfrastructureNetworkingEngine:

    def create(
        self,
        name: str,
        cidr: str
    ):

        return VirtualNetwork(

            network_id=
                "network-001",

            name=
                name,

            cidr=
                cidr,

            isolated=
                True
        )
