from dataclasses import dataclass


@dataclass
class DataIntelligenceAutomation:

    workflow_name: str

    triggers: list[str]

    actions: list[str]


class DataIntelligenceAutomationEngine:

    def generate(
        self
    ):

        return DataIntelligenceAutomation(

            workflow_name=
                "enterprise_data_intelligence",

            triggers=[

                "dataset_updated",

                "schema_changed",

                "new_data_source_registered"
            ],

            actions=[

                "validate_data_quality",

                "update_data_catalog",

                "refresh_lineage_graph",

                "notify_data_stewards"
            ]
        )
