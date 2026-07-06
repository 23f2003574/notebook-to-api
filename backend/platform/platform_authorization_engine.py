from dataclasses import dataclass


@dataclass
class PlatformPermission:

    resource: str

    action: str

    allowed: bool


@dataclass
class AuthorizationResult:

    authorized: bool

    permissions: list[PlatformPermission]


class PlatformAuthorizationEngine:

    def authorize(
        self,
        identity,
        action: str,
        resource: str
    ):

        return AuthorizationResult(

            authorized=True,

            permissions=[

                PlatformPermission(

                    resource=resource,

                    action=action,

                    allowed=True
                )
            ]
        )
