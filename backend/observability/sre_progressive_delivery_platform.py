from dataclasses import dataclass

from .service_level_objective_engine import (
    ServiceLevelObjective,
    ServiceLevelObjectiveEngine
)

from .reliability_aware_delivery_control_plane import (
    DeliveryRuntimeDecision,
    ReliabilityAwareDeliveryControlPlane,
    ReliabilityAwareDeliveryPlan
)

from .post_rollback_verification_engine import (
    PostRollbackVerification
)

from .post_deployment_stability_monitoring_engine import (
    PostDeploymentStability
)


@dataclass
class ServiceReliabilityObjectiveResult:

    objective: ServiceLevelObjective

    status: str


class SREProgressiveDeliveryPlatform:

    def __init__(self):

        self.service_level_objective_engine = (
            ServiceLevelObjectiveEngine()
        )

        self.delivery_control_plane = (
            ReliabilityAwareDeliveryControlPlane()
        )

    def evaluate_reliability_objective(
        self,
        service_name: str,
        indicator_name: str,
        target: float,
        current_value: float
    ):

        objective = (
            self
            .service_level_objective_engine
            .evaluate(
                service_name,
                indicator_name,
                target,
                current_value
            )
        )

        status = (
            "healthy"
            if objective.objective_met
            else "violated"
        )

        return ServiceReliabilityObjectiveResult(

            objective=
                objective,

            status=
                status
        )

    def plan_release(
        self,
        service_name: str,
        change_id: str,
        files_changed: int,
        affected_components: int,
        database_change: bool,
        infrastructure_change: bool,
        slo_target: float,
        total_events: int,
        failed_events: int,
        budget_consumed_percentage: float,
        window_elapsed_percentage: float
    ) -> ReliabilityAwareDeliveryPlan:

        return (
            self
            .delivery_control_plane
            .plan_release(
                service_name,
                change_id,
                files_changed,
                affected_components,
                database_change,
                infrastructure_change,
                slo_target,
                total_events,
                failed_events,
                budget_consumed_percentage,
                window_elapsed_percentage
            )
        )

    def evaluate_rollout(
        self,
        deployment_id: str,
        current_traffic_percentage: int,
        error_rate: float,
        latency_ms: float,
        health_check_passed: bool,
        deployed_version: str,
        previous_stable_version: str
    ) -> DeliveryRuntimeDecision:

        return (
            self
            .delivery_control_plane
            .evaluate_runtime(
                deployment_id,
                current_traffic_percentage,
                error_rate,
                latency_ms,
                health_check_passed,
                deployed_version,
                previous_stable_version
            )
        )

    def verify_rollback(
        self,
        deployment_id: str,
        expected_version: str,
        active_version: str,
        health_check_passed: bool
    ) -> PostRollbackVerification:

        return (
            self
            .delivery_control_plane
            .verify_rollback_recovery(
                deployment_id,
                expected_version,
                active_version,
                health_check_passed
            )
        )

    def evaluate_stability(
        self,
        deployment_id: str,
        error_rate: float,
        latency_ms: float,
        burn_rate: float,
        active_incidents: int
    ) -> PostDeploymentStability:

        return (
            self
            .delivery_control_plane
            .evaluate_stability(
                deployment_id,
                error_rate,
                latency_ms,
                burn_rate,
                active_incidents
            )
        )
