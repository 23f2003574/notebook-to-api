from dataclasses import dataclass
from typing import Dict


@dataclass
class SDKProject:

    files: Dict[
        str,
        str
    ]

    def file_count(
        self
    ):

        return len(
            self.files
        )

    def file_names(
        self
    ):

        return sorted(
            self.files.keys()
        )

    def contains(
        self,
        filename: str
    ):

        return (
            filename
            in self.files
        )

    def deployment_file_count(
        self
    ):

        deployment_files = [

            filename

            for filename
            in self.files

            if (
                "docker" in filename
                or
                "k8s" in filename
            )
        ]

        return len(
            deployment_files
        )


class SDKProjectGenerator:

    def generate_project(
        self,
        package_json: str,
        tsconfig: str,
        sdk_index: str,
        sdk_modules: dict
    ):

        files = {
            "package.json":
                package_json,

            "tsconfig.json":
                tsconfig,

            "src/index.ts":
                sdk_index
        }

        for (
            module_name,
            module_code
        ) in sdk_modules.items():

            files[
                f"src/{module_name}.ts"
            ] = module_code

        return SDKProject(
            files=files
        )