from dataclasses import dataclass
from typing import Dict


@dataclass
class ExecutionMetrics:

    stage_durations: Dict[
        str,
        float
    ]

    total_duration: float

    def slowest_stage(self):

        if not self.stage_durations:
            return None

        return max(
            self.stage_durations,
            key=self.stage_durations.get
        )

    def average_stage_duration(
        self
    ):

        if not self.stage_durations:
            return 0.0

        return (
            sum(
                self.stage_durations.values()
            )
            /
            len(
                self.stage_durations
            )
        )