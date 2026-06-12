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


class PythonPackageGenerator:

    def generate_package(
        self,
        client_code: str,
        request_model: str,
        response_model: str,
        exceptions_code: str
    ):

        models_code = (
            request_model
            +
            "\n\n"
            +
            response_model
        )

        files = {

            "__init__.py":
            (
                "from .client "
                "import *\n"
                "from .models "
                "import *\n"
                "from .exceptions "
                "import *\n"
            ),

            "client.py":
                client_code,

            "models.py":
                models_code,

            "exceptions.py":
                exceptions_code
        }

        return PythonPackage(
            files=files
        )

