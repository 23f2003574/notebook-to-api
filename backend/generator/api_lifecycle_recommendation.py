from dataclasses import dataclass
from typing import List


@dataclass
class APILifecycleRecommendation:
    recommendation: str
    category: str
    priority: str


class APILifecycleRecommendationEngine:
    def generate(self) -> List[APILifecycleRecommendation]:
        return [
            APILifecycleRecommendation(
                recommendation="adopt_semantic_versioning",
                category="versioning",
                priority="high",
            ),
            APILifecycleRecommendation(
                recommendation="publish_deprecation_roadmap",
                category="lifecycle",
                priority="high",
            ),
            APILifecycleRecommendation(
                recommendation="expand_api_portfolio_governance",
                category="governance",
                priority="medium",
            ),
        ]
