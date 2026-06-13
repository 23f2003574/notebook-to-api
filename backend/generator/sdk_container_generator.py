from textwrap import dedent


class SDKContainerGenerator:

    def generate_dockerfile(
        self,
        package_name: str
    ):

        return dedent(
            f"""
            FROM python:3.12-slim

            WORKDIR /app

            COPY . .

            RUN pip install -r requirements.txt

            CMD ["python"]
            """
        )

    def generate_dockerignore(
        self
    ):

        return dedent(
            """
            __pycache__/

            *.pyc

            .venv/

            dist/

            build/
            """
        )