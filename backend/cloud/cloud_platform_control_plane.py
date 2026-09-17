from dataclasses import dataclass


@dataclass
class CloudPlatformStatus:

    initialized: bool

    registered_clusters: int

    infrastructure_ready: bool


class CloudPlatformControlPlane:

    def initialize(
        self
    ):

        return CloudPlatformStatus(

            initialized=
                True,

            registered_clusters=
                0,

            infrastructure_ready=
                True
        )
