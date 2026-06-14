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

    def recommendation_manifest(
        self,
        recommendation
    ):

        return {

            "primary":
                recommendation
                .primary_target,

            "alternatives":
                recommendation
                .alternatives,

            "unsupported":
                recommendation
                .unsupported
        }

    def cost_manifest(
        self,
        costs
    ):

        return {

            cost.target: {

                "complexity":
                    cost.complexity,

                "operational_cost":
                    cost.operational_cost,

                "score":
                    cost.score
            }

            for cost
            in costs
        }

    def deployment_plan_manifest(
        self,
        plan
    ):

        return {

            "recommended":
                plan
                .recommended_target,

            "complexity":
                plan
                .estimated_complexity,

            "fallbacks":
                plan
                .fallback_targets,

            "rationale":
                plan
                .rationale
        }

    def health_manifest(
        self,
        health
    ):

        return {

            "target":
                health.target,

            "healthy":
                health.healthy,

            "score":
                health.score,

            "message":
                health.message
        }

    def readiness_manifest(
        self,
        readiness
    ):

        return {

            "ready":
                readiness.ready,

            "score":
                readiness.score,

            "reasons":
                readiness.reasons
        }

    def risk_manifest(
        self,
        risk
    ):

        return {

            "level":
                risk.level,

            "score":
                risk.score,

            "factors":
                risk.factors
        }