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

        init_file = sdk_dir / "__init__.py"

        init_file.write_text(
            "from .client import APIClient\n"
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

            args = func.get(
                "args",
                []
            )

            arg_names = [
                arg["name"]
                for arg in args
            ]

            signature_args = ", ".join(arg_names)

            payload_entries = []

            for arg_name in arg_names:
                payload_entries.append(
                    f'"{arg_name}": {arg_name}'
                )

            payload_dict = ", ".join(
                payload_entries
            )

            if signature_args:
                method_signature = (
                    f"self, {signature_args}"
                )
            else:
                method_signature = "self"

            methods.append(
                f"""
    def {func_name}(
        {method_signature}
    ):
        payload = {{
            {payload_dict}
        }}

        return self._request(
            "POST",
            "/{func_name}",
            json=payload
        )
"""
            )

        generated_methods = "".join(methods)

        return f'''
import requests


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

        response.raise_for_status()

        if response.content:
            return response.json()

        return None

{generated_methods}
'''