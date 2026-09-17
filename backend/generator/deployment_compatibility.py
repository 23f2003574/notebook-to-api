from dataclasses import dataclass


@dataclass
class DeploymentCompatibility:

    target: str

    supported: bool

    reason: str


class DeploymentCompatibilityAnalyzer:

    TARGET_REQUIREMENTS = {

        "docker": [
            "Dockerfile"
        ],

        "docker-compose": [
            "docker-compose.yml"
        ],

        "kubernetes": [
            "k8s-deployment.yaml",
            "k8s-service.yaml"
        ],

        "helm": [
            "Chart.yaml",
            "values.yaml"
        ],

        "terraform": [
            "main.tf",
            "variables.tf",
            "outputs.tf"
        ]
    }

    def analyze(
        self,
        project
    ):

        results = []

        for (
            target,
            requirements
        ) in (
            self.TARGET_REQUIREMENTS
            .items()
        ):

            missing = [

                artifact

                for artifact
                in requirements

                if artifact
                not in project.files
            ]

            results.append(
                DeploymentCompatibility(
                    target=target,

                    supported=
                        len(missing)
                        == 0,

                    reason=
                        "Ready"
                        if not missing
                        else (
                            "Missing: "
                            +
                            ", ".join(
                                missing
                            )
                        )
                )
            )

        return results