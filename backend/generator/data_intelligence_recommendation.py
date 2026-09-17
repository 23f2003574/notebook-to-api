from dataclasses import dataclass


@dataclass
class DataIntelligenceRecommendation:

    recommendation: str

    category: str

    priority: str


class DataIntelligenceRecommendationEngine:

    def generate(
        self
    ):

        return [

            DataIntelligenceRecommendation(

                recommendation=
                    "increase_data_quality_validation",

                category=
                    "data_quality",

                priority=
                    "high"
            ),

            DataIntelligenceRecommendation(

                recommendation=
                    "expand_lineage_tracking",

                category=
                    "data_lineage",

                priority=
                    "high"
            ),

            DataIntelligenceRecommendation(

                recommendation=
                    "strengthen_data_governance",

                category=
                    "governance",

                priority=
                    "medium"
            )
        ]
