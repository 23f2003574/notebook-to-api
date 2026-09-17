from dataclasses import dataclass


@dataclass
class PlatformCapability:

    capability_name: str

    provider: str

    version: str


class PlatformCapabilityRegistry:

    def lookup(
        self,
        capability: str
    ):

        return [

            PlatformCapability(

                capability_name=
                    capability,

                provider=
                    "compiler",

                version=
                    "1.0.0"
            )
        ]
