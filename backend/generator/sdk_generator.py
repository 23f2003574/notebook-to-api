from pathlib import Path


class SDKGenerator:
    """
    Generates SDK clients from analyzed notebook functions.
    """

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def generate(
        self,
        functions,
        language: str = "python"
    ):
        if language == "python":
            return self._generate_python_sdk(functions)

        raise ValueError(
            f"Unsupported SDK language: {language}"
        )

    def _generate_python_sdk(
        self,
        functions
    ):
        sdk_dir = self.output_dir / "python_sdk"

        sdk_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        models_file = sdk_dir / "models.py"

        models_file.write_text(
            self._build_models_file(functions)
        )

        exceptions_file = sdk_dir / "exceptions.py"

        exceptions_file.write_text(
            self._build_exceptions_file()
        )

        readme_file = sdk_dir / "README.md"

        readme_file.write_text(
            self._build_readme(functions)
        )

        init_file = sdk_dir / "__init__.py"

        init_file.write_text(
            "from .client import APIClient\n"
            "from .models import *\n"
            "from .exceptions import *\n"
        )

        client_file = sdk_dir / "client.py"

        client_file.write_text(
            self._build_client_template(functions)
        )

        return sdk_dir

    def _build_client_template(
        self,
        functions
    ):
        methods = []

        for func in functions:
            func_name = func["name"]

            response_model_name = (
                "".join(
                    part.capitalize()
                    for part in func_name.split("_")
                ) + "Response"
            )

            request_model_name = (
                "".join(
                    part.capitalize()
                    for part in func_name.split("_")
                ) + "Request"
            )

            method_signature = (
                f"self, request: {request_model_name}"
            )

            methods.append(
                f"""
    def {func_name}(
        {method_signature}
    ) -> {response_model_name}:
        payload = request.to_dict()

        response = self._request(
            "POST",
            "/{func_name}",
            json=payload
        )

        return {response_model_name}(**response)
"""
            )

        generated_methods = "".join(methods)

        infrastructure_methods = """
    def health(self):
        return self._request(
            "GET",
            "/health"
        )

    def ready(self):
        return self._request(
            "GET",
            "/ready"
        )

    def info(self):
        return self._request(
            "GET",
            "/info"
        )

    def metrics(self):
        return self._request(
            "GET",
            "/metrics"
        )

    def uptime(self):
        return self._request(
            "GET",
            "/uptime"
        )

    def list_tasks(self):
        return self._request(
            "GET",
            "/tasks"
        )

    def get_task(
        self,
        task_id: str
    ):
        return self._request(
            "GET",
            f"/tasks/{task_id}"
        )

    def cleanup_tasks(self):
        return self._request(
            "POST",
            "/tasks/cleanup"
        )

    def reset_tasks(self):
        return self._request(
            "POST",
            "/tasks/reset"
        )

    def delete_task(
        self,
        task_id: str
    ):
        return self._request(
            "DELETE",
            f"/tasks/{task_id}"
        )
"""

        return f'''
import requests
from typing import Any

from .models import *
from .exceptions import *


class APIClient:
    def __init__(
        self,
        base_url,
        api_key=None,
        timeout=30
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self):
        headers = {{}}

        if self.api_key:
            headers["X-API-Key"] = self.api_key

        return headers

    def _request(
        self,
        method,
        endpoint,
        **kwargs
    ):
        response = requests.request(
            method=method,
            url=f"{{self.base_url}}{{endpoint}}",
            headers=self._headers(),
            timeout=self.timeout,
            **kwargs
        )

        if response.status_code == 401:
            raise AuthenticationError(
                response.text
            )

        if response.status_code == 404:
            raise NotFoundError(
                response.text
            )

        if response.status_code >= 500:
            raise ServerError(
                response.text
            )

        if response.status_code >= 400:
            raise APIError(
                response.text
            )

        if response.content:
            return response.json()

        return None

{infrastructure_methods}

{generated_methods}
'''

    def _build_models_file(
        self,
        functions
    ):
        lines = [
            "from dataclasses import dataclass",
            "from typing import Optional, Any",
            "from .exceptions import ValidationError",
            "",
        ]

        for func in functions:
            func_name = func["name"]

            request_model_name = (
                "".join(
                    part.capitalize()
                    for part in func_name.split("_")
                ) + "Request"
            )

            lines.extend([
                "@dataclass",
                f"class {request_model_name}:"
            ])

            args = func.get(
                "args",
                []
            )

            if not args:
                lines.append("    pass")
            else:
                _MISSING = object()

                TYPE_MAPPING = {
                    "int": "int",
                    "float": "float",
                    "str": "str",
                    "bool": "bool",
                    "list": "list",
                    "dict": "dict",
                    "Optional[int]": "Optional[int]",
                    "Optional[float]": "Optional[float]",
                    "Optional[str]": "Optional[str]",
                    "Optional[bool]": "Optional[bool]"
                }

                for arg in args:
                    arg_name = arg["name"]

                    arg_type = arg.get(
                        "type",
                        "Any"
                    )

                    default_value = arg.get(
                        "default",
                        _MISSING
                    )

                    python_type = TYPE_MAPPING.get(
                        arg_type,
                        "Any"
                    )

                    if default_value is _MISSING:
                        lines.append(
                            f"    {arg_name}: {python_type}"
                        )
                    else:
                        lines.append(
                            f"    {arg_name}: {python_type} = {repr(default_value)}"
                        )

                typed_args = [
                    (
                        arg["name"],
                        TYPE_MAPPING.get(
                            arg.get("type", "Any"),
                            "Any"
                        ),
                        arg.get("default", _MISSING)
                    )
                    for arg in args
                ]

                required_validatable = [
                    (name, ptype)
                    for name, ptype, _default
                    in typed_args
                    if ptype != "Any"
                    and not ptype.startswith("Optional")
                ]

                optional_validatable = [
                    (name, ptype)
                    for name, ptype, _default
                    in typed_args
                    if ptype.startswith("Optional")
                ]

                lines.extend([
                    "",
                    "    def __post_init__(self):"
                ])

                if not required_validatable and not optional_validatable:
                    lines.append("        pass")
                else:
                    for arg_name, python_type in required_validatable:
                        lines.append(
                            f"        if not isinstance(self.{arg_name}, {python_type}):"
                        )

                        lines.append(
                            f'            raise ValidationError("{arg_name} must be of type {python_type}")'
                        )

                    for arg_name, python_type in optional_validatable:
                        inner_type = (
                            python_type
                            .replace("Optional[", "")
                            .replace("]", "")
                        )

                        lines.append(
                            f"        if self.{arg_name} is not None and not isinstance(self.{arg_name}, {inner_type}):"
                        )

                        lines.append(
                            f'            raise ValidationError("{arg_name} must be Optional[{inner_type}]")'
                        )

                lines.extend([
                    "",
                    "    def to_dict(self):",
                    "        return {"
                ])

                for arg in args:
                    arg_name = arg["name"]

                    lines.append(
                        f'            "{arg_name}": self.{arg_name},'
                    )

                lines.extend([
                    "        }",
                    ""
                ])

            lines.append("")

            response_model_name = (
                "".join(
                    part.capitalize()
                    for part in func_name.split("_")
                ) + "Response"
            )

            return_type = func.get(
                "return_type",
                "Any"
            )

            type_mapping = {
                "int": "int",
                "float": "float",
                "str": "str",
                "bool": "bool",
                "list": "list",
                "dict": "dict"
            }

            python_type = type_mapping.get(
                return_type,
                "Any"
            )

            lines.extend([
                "@dataclass",
                f"class {response_model_name}:",
                f"    result: {python_type}",
                ""
            ])

        return "\n".join(lines)

    def _build_exceptions_file(self):
        return """
class APIError(Exception):
    pass


class ValidationError(APIError):
    pass


class AuthenticationError(APIError):
    pass


class NotFoundError(APIError):
    pass


class ServerError(APIError):
    pass
"""

    def _build_readme(
        self,
        functions
    ):
        lines = [
            "# Generated Python SDK",
            "",
            "## Installation",
            "",
            "```bash",
            "pip install requests",
            "```",
            "",
            "## Usage",
            "",
            "```python",
            "from python_sdk import APIClient",
            "",
            "client = APIClient(",
            "    base_url='http://localhost:8000',",
            "    api_key='your-api-key'",
            ")",
            "```",
            "",
            "## Available Methods",
            ""
        ]

        for func in functions:
            lines.append(
                f"- `client.{func['name']}(...)`"
            )

        lines.extend([
            "",
            "## Infrastructure Methods",
            "",
            "- `client.health()`",
            "- `client.ready()`",
            "- `client.info()`",
            "- `client.metrics()`",
            "- `client.uptime()`",
            "",
            "## Task Management",
            "",
            "- `client.list_tasks()`",
            "- `client.get_task(task_id)`",
            "- `client.cleanup_tasks()`",
            "- `client.reset_tasks()`",
            "- `client.delete_task(task_id)`",
        ])

        return "\n".join(lines)