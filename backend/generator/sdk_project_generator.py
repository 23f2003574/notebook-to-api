from dataclasses import dataclass
from typing import Dict


@dataclass
class SDKProject:

    files: Dict[
        str,
        str
    ]

    def file_count(
        self
    ):

        return len(
            self.files
        )

    def file_names(
        self
    ):

        return sorted(
            self.files.keys()
        )

    def contains(
        self,
        filename: str
    ):

        return (
            filename
            in self.files
        )

    def deployment_file_count(
        self
    ):

        deployment_files = [

            filename

            for filename
            in self.files

            if (
                "docker" in filename
                or
                "k8s" in filename
            )
        ]

        return len(
            deployment_files
        )

    def workflow_count(
        self
    ):

        workflow_files = [

            filename

            for filename
            in self.files

            if (
                "workflow" in filename
                or
                "action" in filename
            )
        ]

        return len(
            workflow_files
        )

    def supports_target(
        self,
        target: str
    ):

        deployment_files = {

            "docker":
                "Dockerfile",

            "helm":
                "Chart.yaml",

            "kubernetes":
                "k8s-deployment.yaml"
        }

        expected = (
            deployment_files.get(
                target
            )
        )

        if expected is None:

            return False

        return expected in self.files

    def infrastructure_file_count(
        self
    ):

        return len(
            [
                filename

                for filename
                in self.files

                if filename.endswith(
                    ".tf"
                )
            ]
        )

    def cloud_target_count(
        self
    ):

        cloud_files = [

            filename

            for filename
            in self.files

            if (
                "aws" in filename
                or
                "azure" in filename
                or
                "gcp" in filename
            )
        ]

        return len(
            cloud_files
        )

    def validation_ready(
        self
    ):

        required = [

            "Dockerfile",

            "docker-compose.yml"
        ]

        return all(
            filename
            in self.files
            for filename
            in required
        )

    def supported_targets(
        self,
        compatibility_results
    ):

        return [

            result.target

            for result
            in compatibility_results

            if result.supported
        ]

    def recommended_target(
        self,
        recommendation
    ):

        return (
            recommendation
            .primary_target
        )

    def cheapest_target(
        self,
        costs
    ):

        if not costs:

            return None

        return costs[0].target

    def deployment_strategy(
        self,
        plan
    ):

        return (
            plan
            .recommended_target
        )

    def deployment_health_score(
        self,
        health
    ):

        return (
            health.score
        )


class SDKProjectGenerator:

    def generate_project(
        self,
        package_json: str,
        tsconfig: str,
        sdk_index: str,
        sdk_modules: dict
    ):

        files = {
            "package.json":
                package_json,

            "tsconfig.json":
                tsconfig,

            "src/index.ts":
                sdk_index
        }

        for (
            module_name,
            module_code
        ) in sdk_modules.items():

            files[
                f"src/{module_name}.ts"
            ] = module_code

        return SDKProject(
            files=files
        )