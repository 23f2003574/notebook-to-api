from dataclasses import dataclass


@dataclass
class SDKRelease:

    package_name: str

    version: str

    release_tag: str

    release_notes: str


class SDKReleaseEngine:

    def generate(
        self,
        package_name,
        version
    ):

        return SDKRelease(

            package_name=
                package_name,

            version=
                version,

            release_tag=
                f"v{version}",

            release_notes=
                (
                    f"Release "
                    f"{version}"
                )
        )
