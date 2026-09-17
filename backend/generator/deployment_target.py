from dataclasses import dataclass


@dataclass
class DeploymentTarget:

    name: str

    deployment_type: str

    confidence: float


class DeploymentTargetEngine:

    def generate(
        self
    ):

        return [

            DeploymentTarget(

                name=
                    "Railway",

                deployment_type=
                    "Container",

                confidence=
                    0.95
            ),

            DeploymentTarget(

                name=
                    "Render",

                deployment_type=
                    "Container",

                confidence=
                    0.90
            ),

            DeploymentTarget(

                name=
                    "Fly.io",

                deployment_type=
                    "Container",

                confidence=
                    0.85
            ),

            DeploymentTarget(

                name=
                    "Docker",

                deployment_type=
                    "Self Hosted",

                confidence=
                    0.80
            )
        ]
