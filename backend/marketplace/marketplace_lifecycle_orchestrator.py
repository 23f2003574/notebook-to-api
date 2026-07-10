from dataclasses import dataclass


@dataclass
class MarketplaceLifecycle:

    extension_id: str

    registered: bool

    published: bool

    installable: bool


class MarketplaceLifecycleOrchestrator:

    def initialize(
        self,
        extension_id: str
    ):

        return MarketplaceLifecycle(

            extension_id=
                extension_id,

            registered=
                True,

            published=
                False,

            installable=
                False
        )
