from dataclasses import dataclass


@dataclass
class ReleaseGateDecision:

    service_name: str

    error_budget_exhausted: bool

    burn_rate: float

    release_allowed: bool

    reason: str


class ReliabilityAwareReleaseGatingEngine:

    def evaluate(
        self,
        service_name: str,
        error_budget_exhausted: bool,
        burn_rate: float
    ):

        release_allowed = (
            not error_budget_exhausted
            and burn_rate <= 1.0
        )

        if error_budget_exhausted:

            reason = (
                "release blocked because "
                "the error budget is exhausted"
            )

        elif burn_rate > 1.0:

            reason = (
                "release blocked because "
                "the error budget burn rate is too high"
            )

        else:

            reason = (
                "release allowed because "
                "service reliability is within policy"
            )

        return ReleaseGateDecision(

            service_name=
                service_name,

            error_budget_exhausted=
                error_budget_exhausted,

            burn_rate=
                burn_rate,

            release_allowed=
                release_allowed,

            reason=
                reason
        )
