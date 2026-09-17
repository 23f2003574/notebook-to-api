from dataclasses import dataclass


@dataclass
class PlatformExtension:

    name: str

    version: str

    capabilities: list[str]


class PlatformExtensionSdk:

    def register_extension(
        self,
        extension: PlatformExtension
    ):

        return {

            "registered": True,

            "extension": extension.name
        }
