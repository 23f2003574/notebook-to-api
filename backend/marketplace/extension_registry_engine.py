from dataclasses import dataclass


@dataclass
class PlatformExtension:

    extension_id: str

    name: str

    version: str

    publisher: str


class ExtensionRegistryEngine:

    def register(
        self,
        name: str,
        version: str,
        publisher: str
    ):

        return PlatformExtension(

            extension_id=
                "extension-001",

            name=
                name,

            version=
                version,

            publisher=
                publisher
        )
