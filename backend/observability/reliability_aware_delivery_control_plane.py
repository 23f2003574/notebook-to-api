from dataclasses import dataclass
from typing import Optional

from .change_risk_assessment_engine import (
    ChangeRiskAssessment,
    ChangeRiskAssessmentEngine
)

from .error_budget_management_engine import (
    ErrorBudget,
    ErrorBudgetManagementEngine
)

from .error_budget_burn_rate_engine import (
    ErrorBudgetBurnRate,
    ErrorBudgetBurnRateEngine
)

from .reliability_aware_release_gating_engine import (
    ReleaseGateDecision,
    ReliabilityAwareReleaseGatingEngine
)

from .progressive_delivery_strategy_engine import (
    ProgressiveDeliveryStrategy,
    ProgressiveDeliveryStrategyEngine
)

from .deployment_health_verification_engine import (
    DeploymentHealthVerification,
    DeploymentHealthVerificationEngine
)

from .progressive_rollout_promotion_engine import (
    RolloutPromotionDecision,
    ProgressiveRolloutPromotionEngine
)

from .automated_deployment_rollback_engine import (
    DeploymentRollback,
    AutomatedDeploymentRollbackEngine
)

from .post_rollback_verification_engine import (
    PostRollbackVerification,
    PostRollbackVerificationEngine
)

from .post_deployment_stability_monitoring_engine import (
    PostDeploymentStability,
    PostDeploymentStabilityMonitoringEngine
)


@dataclass
class ReliabilityAwareDeliveryPlan:

    service_name: str

    change_risk: ChangeRiskAssessment

    error_budget: ErrorBudget

    burn_rate: ErrorBudgetBurnRate

    release_gate: ReleaseGateDecision

    delivery_strategy: Optional[
        ProgressiveDeliveryStrategy
    ]


@dataclass
class DeliveryRuntimeDecision:

    deployment_health: DeploymentHealthVerification

    promotion_decision: RolloutPromotionDecision

    rollback_decision: DeploymentRollback


class ReliabilityAwareDeliveryControlPlane:

    def __init__(self):

        self.change_risk_engine = (
            ChangeRiskAssessmentEngine()
        )

        self.error_budget_engine = (
            ErrorBudgetManagementEngine()
        )

        self.burn_rate_engine = (
            ErrorBudgetBurnRateEngine()
        )

        self.release_gate_engine = (
            ReliabilityAwareReleaseGatingEngine()
        )

        self.delivery_strategy_engine = (
            ProgressiveDeliveryStrategyEngine()
        )

        self.deployment_health_engine = (
            DeploymentHealthVerificationEngine()
        )

        self.rollout_promotion_engine = (
            ProgressiveRolloutPromotionEngine()
        )

        self.deployment_rollback_engine = (
            AutomatedDeploymentRollbackEngine()
        )

        self.post_rollback_verification_engine = (
            PostRollbackVerificationEngine()
        )

        self.post_deployment_stability_engine = (
            PostDeploymentStabilityMonitoringEngine()
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
    ):

        change_risk = (
            self
            .change_risk_engine
            .assess(
                change_id,
                files_changed,
                affected_components,
                database_change,
                infrastructure_change
            )
        )

        error_budget = (
            self
            .error_budget_engine
            .calculate(
                service_name,
                slo_target,
                total_events,
                failed_events
            )
        )

        burn_rate = (
            self
            .burn_rate_engine
            .calculate(
                service_name,
                budget_consumed_percentage,
                window_elapsed_percentage
            )
        )

        release_gate = (
            self
            .release_gate_engine
            .evaluate(
                service_name,
                error_budget.exhausted,
                burn_rate.burn_rate
            )
        )

        delivery_strategy = None

        if release_gate.release_allowed:

            delivery_strategy = (
                self
                .delivery_strategy_engine
                .select(
                    service_name,
                    change_risk.risk_level
                )
            )

        return ReliabilityAwareDeliveryPlan(

            service_name=
                service_name,

            change_risk=
                change_risk,

            error_budget=
                error_budget,

            burn_rate=
                burn_rate,

            release_gate=
                release_gate,

            delivery_strategy=
                delivery_strategy
        )

    def evaluate_runtime(
        self,
        deployment_id: str,
        current_traffic_percentage: int,
        error_rate: float,
        latency_ms: float,
        health_check_passed: bool,
        deployed_version: str,
        previous_stable_version: str
    ):

        deployment_health = (
            self
            .deployment_health_engine
            .verify(
                deployment_id,
                error_rate,
                latency_ms,
                health_check_passed
            )
        )

        promotion_decision = (
            self
            .rollout_promotion_engine
            .evaluate(
                deployment_id,
                current_traffic_percentage,
                deployment_health.healthy
            )
        )

        rollback_decision = (
            self
            .deployment_rollback_engine
            .evaluate(
                deployment_id,
                deployed_version,
                previous_stable_version,
                deployment_health.healthy
            )
        )

        return DeliveryRuntimeDecision(

            deployment_health=
                deployment_health,

            promotion_decision=
                promotion_decision,

            rollback_decision=
                rollback_decision
        )

    def verify_rollback_recovery(
        self,
        deployment_id: str,
        expected_version: str,
        active_version: str,
        health_check_passed: bool
    ):

        return (
            self
            .post_rollback_verification_engine
            .verify(
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
    ):

        return (
            self
            .post_deployment_stability_engine
            .evaluate(
                deployment_id,
                error_rate,
                latency_ms,
                burn_rate,
                active_incidents
            )
        )
