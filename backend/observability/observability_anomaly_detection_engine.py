from dataclasses import dataclass


@dataclass
class AnomalyDetectionResult:

    signal_name: str

    observed_value: float

    expected_value: float

    anomalous: bool


class ObservabilityAnomalyDetectionEngine:

    def detect(
        self,
        signal_name: str,
        observed_value: float,
        expected_value: float
    ):

        return AnomalyDetectionResult(

            signal_name=
                signal_name,

            observed_value=
                observed_value,

            expected_value=
                expected_value,

            anomalous=
                observed_value > expected_value
        )
