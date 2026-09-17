from dataclasses import dataclass


@dataclass
class MarketplaceSearchResult:

    extension_id: str

    name: str

    publisher: str

    category: str


@dataclass
class DiscoveryResults:

    query: str

    results: list[MarketplaceSearchResult]


class MarketplaceDiscoveryEngine:

    def search(
        self,
        query: str
    ):

        return DiscoveryResults(

            query=
                query,

            results=[]
        )
