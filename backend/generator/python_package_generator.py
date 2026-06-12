from dataclasses import dataclass
from typing import Dict


@dataclass
class PythonPackage:

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

    def contains_file(
        self,
        filename: str
    ):

        return (
            filename
            in self.files
        )

    def has_client(
        self
    ):

        return (
            "client.py"
            in self.files
        )

    def manifest(
        self
    ):

        return {

            "file_count":
                self.file_count(),

            "files":
                self.file_names()
        }




class PythonPackageGenerator:

    def generate_package(
        self,
        client_code: str,
        async_client_code: str,
        request_model: str,
        response_model: str,
        pagination_code: str,
        exceptions_code: str,
        readme_content: str,
        pyproject_content: str,
        requirements_content: str
    ):

        models_code = (
            request_model
            +
            "\n\n"
            +
            response_model
            +
            "\n\n"
            +
            pagination_code
        )

        files = {

            "README.md":
                readme_content,

            "pyproject.toml":
                pyproject_content,

            "requirements.txt":
                requirements_content,

            "__init__.py":
            (
                "from .client "
                "import *\n"
                "from .async_client "
                "import *\n"
                "from .models "
                "import *\n"
                "from .exceptions "
                "import *\n"
            ),

            "client.py":
                client_code,

            "async_client.py":
                async_client_code,

            "models.py":
                models_code,

            "exceptions.py":
                exceptions_code
        }

        return PythonPackage(
            files=files
        )


