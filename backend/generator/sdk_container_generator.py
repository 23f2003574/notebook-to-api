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

    def generate_docker_compose(
        self,
        package_name: str
    ):

        return dedent(
            f"""
            version: "3.9"

            services:

              {package_name}:

                build: .

                container_name:
                  {package_name}

                restart:
                  unless-stopped
            """
        )

    def generate_env_file(
        self
    ):

        return dedent(
            """
            API_URL=http://localhost

            LOG_LEVEL=INFO
            """
        )

    def generate_kubernetes_deployment(
        self,
        package_name: str
    ):

        return dedent(
            f"""
            apiVersion: apps/v1

            kind: Deployment

            metadata:

              name:
                {package_name}

            spec:

              replicas: 1

              selector:

                matchLabels:

                  app:
                    {package_name}

              template:

                metadata:

                  labels:

                    app:
                      {package_name}

                spec:

                  containers:

                  - name:
                      {package_name}

                    image:
                      {package_name}:latest

                    ports:

                    - containerPort: 8000
            """
        )

    def generate_kubernetes_service(
        self,
        package_name: str
    ):

        return dedent(
            f"""
            apiVersion: v1

            kind: Service

            metadata:

              name:
                {package_name}

            spec:

              selector:

                app:
                  {package_name}

              ports:

              - port: 80

                targetPort: 8000

              type: ClusterIP
            """
        )

    def generate_github_actions(
        self
    ):

        return dedent(
            """
            name: SDK Release

            on:

              push:

                branches:

                  - main

            jobs:

              build:

                runs-on:
                  ubuntu-latest

                steps:

                - uses:
                    actions/checkout@v4

                - uses:
                    actions/setup-python@v5

                  with:

                    python-version:
                      "3.12"

                - run:
                    pip install -r requirements.txt

                - run:
                    pytest
            """
        )

    def generate_release_workflow(
        self
    ):

        return dedent(
            """
            name: Release

            on:

              workflow_dispatch:

            jobs:

              publish:

                runs-on:
                  ubuntu-latest

                steps:

                - uses:
                    actions/checkout@v4

                - run:
                    echo "Publishing SDK"
            """
        )