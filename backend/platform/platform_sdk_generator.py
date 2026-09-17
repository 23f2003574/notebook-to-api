from dataclasses import dataclass


@dataclass
class GeneratedSdk:

    language: str

    package_name: str

    version: str


class PlatformSdkGenerator:

    def generate(
        self,
        language: str
    ):

        return GeneratedSdk(

            language=
                language,

            package_name=
                f"notebook2api-{language}",

            version=
                "1.0.0"
        )
