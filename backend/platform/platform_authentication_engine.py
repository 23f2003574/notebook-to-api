from dataclasses import dataclass


@dataclass
class PlatformIdentity:

    subject: str

    authentication_method: str

    authenticated: bool


class PlatformAuthenticationEngine:

    def authenticate(
        self,
        credentials: dict
    ):

        return PlatformIdentity(

            subject=
                credentials.get(
                    "subject",
                    "anonymous"
                ),

            authentication_method=
                credentials.get(
                    "method",
                    "none"
                ),

            authenticated=
                True
        )
