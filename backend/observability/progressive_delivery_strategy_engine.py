from dataclasses import dataclass


@dataclass
class ProgressiveDeliveryStrategy:

    service_name: str

    risk_level: str

    strategy: str

    initial_traffic_percentage: int

    requires_verification: bool


class ProgressiveDeliveryStrategyEngine:

    def select(
        self,
        service_name: str,
        risk_level: str
    ):

        normalized_risk_level = (
            risk_level
            .strip()
            .lower()
        )

        if normalized_risk_level == "critical":

            strategy = (
                "controlled_canary"
            )

            initial_traffic_percentage = (
                1
            )

            requires_verification = (
                True
            )

        elif normalized_risk_level == "high":

            strategy = (
                "canary"
            )

            initial_traffic_percentage = (
                5
            )

            requires_verification = (
                True
            )

        elif normalized_risk_level == "medium":

            strategy = (
                "rolling"
            )

            initial_traffic_percentage = (
                25
            )

            requires_verification = (
                True
            )

        else:

            strategy = (
                "direct"
            )

            initial_traffic_percentage = (
                100
            )

            requires_verification = (
                False
            )

        return ProgressiveDeliveryStrategy(

            service_name=
                service_name,

            risk_level=
                normalized_risk_level,

            strategy=
                strategy,

            initial_traffic_percentage=
                initial_traffic_percentage,

            requires_verification=
                requires_verification
        )
