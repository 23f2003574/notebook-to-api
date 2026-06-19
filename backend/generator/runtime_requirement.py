from dataclasses import dataclass


@dataclass
class RuntimeRequirement:

    language: str

    version: str

    framework: str

    framework_version: str


class RuntimeRequirementEngine:

    def generate(
        self
    ):

        return RuntimeRequirement(

            language=
                "python",

            version=
                "3.11",

            framework=
                "fastapi",

            framework_version=
                "latest"
        )
