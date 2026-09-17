from dataclasses import dataclass


@dataclass
class DeploymentBlueprint:

    target: str

    runtime: str

    entrypoint: str

    port: int


class DeploymentBlueprintEngine:

    def generate(
        self,
        target
    ):

        return DeploymentBlueprint(

            target=
                target,

            runtime=
                "python",

            entrypoint=
                "uvicorn app:app --host 0.0.0.0 --port 8000",

            port=
                8000
        )
