from dataclasses import dataclass


@dataclass
class EnterpriseIntegration:

    integration_pattern: str

    messaging_strategy: str

    api_gateway_required: bool

    event_streaming_enabled: bool


class EnterpriseIntegrationIntelligenceEngine:

    def generate(
        self
    ):

        return EnterpriseIntegration(
            integration_pattern=
                "event_driven",
            messaging_strategy=
                "publish_subscribe",
            api_gateway_required=
                True,
            event_streaming_enabled=
                True
        )
