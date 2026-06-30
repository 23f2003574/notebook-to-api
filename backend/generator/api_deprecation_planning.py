from dataclasses import dataclass


@dataclass
class APIDeprecationPlan:
    deprecated_version: str
    replacement_version: str
    sunset_period_days: int
    migration_strategy: str


class APIDeprecationPlanningEngine:
    def generate(self):
        return APIDeprecationPlan(
            deprecated_version="v1",
            replacement_version="v2",
            sunset_period_days=180,
            migration_strategy="parallel_version_support",
        )
