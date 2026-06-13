from dataclasses import dataclass

from datetime import datetime, timezone


@dataclass
class SDKReleaseMetadata:

    package_name: str

    version: str

    generated_at: str

    artifact_count: int


class SDKReleaseGenerator:

    def generate_release_metadata(
        self,
        package_name: str,
        artifact_count: int
    ):

        return SDKReleaseMetadata(
            package_name=
                package_name,

            version=
                "1.0.0",

            generated_at=
                datetime.now(timezone.utc)
                .isoformat(),

            artifact_count=
                artifact_count
        )

    def generate_manifest(
        self,
        package
    ):

        return {

            "artifact_count":
                package.file_count(),

            "artifacts":
                package.file_names()
        }

    def deployment_manifest(
        self,
        deployment_targets
    ):

        return {

            "targets":
                deployment_targets,

            "count":
                len(
                    deployment_targets
                )
        }

    def infrastructure_manifest(
        self,
        targets
    ):

        return {

            "infrastructure":
                targets,

            "count":
                len(
                    targets
                )
        }

    def cicd_manifest(
        self,
        workflows
    ):

        return {

            "workflow_count":
                len(
                    workflows
                ),

            "workflows":
                workflows
        }

    def deployment_package_manifest(
        self,
        targets
    ):

        return {

            "deployment_targets":
                targets,

            "supports_helm":
                "helm" in targets
        }

    def infrastructure_as_code_manifest(
        self,
        targets
    ):

        return {

            "iac_targets":
                targets,

            "supports_terraform":
                "terraform"
                in targets
        }

    def cloud_manifest(
        self,
        targets
    ):

        return {

            "cloud_targets":
                targets,

            "multi_cloud":
                len(
                    targets
                ) > 1
        }

    def validation_manifest(
        self,
        validation_results
    ):

        passed = len(
            [
                result
                for result
                in validation_results
                if result.passed
            ]
        )

        return {

            "total":
                len(
                    validation_results
                ),

            "passed":
                passed,

            "failed":
                (
                    len(
                        validation_results
                    )
                    -
                    passed
                )
        }

    def validation_summary(
        self,
        results
    ):

        return {

            result.target:
            result.passed

            for result
            in results
        }

    def compatibility_manifest(
        self,
        compatibility_results
    ):

        return {

            result.target:
            result.supported

            for result
            in compatibility_results
        }