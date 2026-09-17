from dataclasses import dataclass


@dataclass
class SDKPackage:

    package_name: str

    version: str

    language: str

    install_command: str


class SDKPackagingEngine:

    def generate(
        self,
        package_name,
        version,
        language
    ):

        install_command = ""

        if language == "python":

            install_command = (
                f"pip install "
                f"{package_name}"
            )

        elif language == "typescript":

            install_command = (
                f"npm install "
                f"{package_name}"
            )

        return SDKPackage(

            package_name=
                package_name,

            version=
                version,

            language=
                language,

            install_command=
                install_command
        )
