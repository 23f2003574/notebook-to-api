from dataclasses import dataclass


@dataclass
class SDKQuickStart:

    package_name: str

    install_command: str

    example_code: str


class SDKQuickStartGenerator:

    def generate(
        self,
        sdk_project
    ):

        package_name = (
            sdk_project.name
        )

        return SDKQuickStart(

            package_name=
                package_name,

            install_command=
                (
                    f"pip install "
                    f"{package_name}"
                ),

            example_code=
                (
                    "from sdk import Client\n"
                    "client = Client()\n"
                    "result = client.call()"
                )
        )
