from dataclasses import dataclass


@dataclass
class VerifiedPublisher:

    publisher: str

    verified: bool

    verification_level: str


@dataclass
class VerificationResult:

    extension_id: str

    trusted: bool

    publisher_verified: bool


class MarketplaceTrustVerificationEngine:

    def verify(
        self,
        extension_id: str,
        publisher: str
    ):

        return VerificationResult(

            extension_id=
                extension_id,

            trusted=
                True,

            publisher_verified=
                True
        )
