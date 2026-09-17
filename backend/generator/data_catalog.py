from dataclasses import dataclass


@dataclass
class DataCatalog:

    dataset_name: str

    business_domain: str

    asset_type: str

    certified: bool


class DataCatalogIntelligenceEngine:

    def generate(
        self
    ):

        return DataCatalog(

            dataset_name=
                "notebook_api_dataset",

            business_domain=
                "analytics",

            asset_type=
                "structured_dataset",

            certified=
                True
        )
