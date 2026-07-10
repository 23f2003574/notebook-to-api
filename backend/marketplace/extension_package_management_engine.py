from dataclasses import dataclass


@dataclass
class ExtensionPackage:

    package_id: str

    extension_id: str

    version: str

    checksum: str


@dataclass
class InstallationResult:

    installed: bool

    package_id: str


class ExtensionPackageManagementEngine:

    def install(
        self,
        package: ExtensionPackage
    ):

        return InstallationResult(

            installed=
                True,

            package_id=
                package.package_id
        )
