from dataclasses import dataclass


@dataclass
class GeneratedArtifact:

    target: str

    filename: str

    generated: bool


@dataclass
class BackendGenerationResult:

    artifacts: list[GeneratedArtifact]


class BackendCodeGenerationEngine:

    def generate(
        self,
        ir
    ):

        return BackendGenerationResult(

            artifacts=[

                GeneratedArtifact(

                    target=
                        "fastapi",

                    filename=
                        "api.py",

                    generated=
                        True
                ),

                GeneratedArtifact(

                    target=
                        "docker",

                    filename=
                        "Dockerfile",

                    generated=
                        True
                ),

                GeneratedArtifact(

                    target=
                        "openapi",

                    filename=
                        "openapi.json",

                    generated=
                        True
                )
            ]
        )
