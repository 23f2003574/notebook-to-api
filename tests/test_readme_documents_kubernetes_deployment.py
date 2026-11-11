import re

import pytest

from backend.generator.api_generator import GENERATED_APP_ENV_VARS
from backend.generator.docker_generator import readme_content
from backend.generator.kubernetes_generator import kubernetes_manifest_content


@pytest.mark.parametrize("name", ["generated", "my_app", "café"])
def test_readme_commands_match_the_generated_manifest(name):
    readme = readme_content(name, [], GENERATED_APP_ENV_VARS)
    manifest = kubernetes_manifest_content(name, GENERATED_APP_ENV_VARS)
    image = re.search(r"^\s+image: \"?([^\"\n]+)", manifest, re.M).group(1)
    secret = re.search(r"kubectl create secret generic (\S+)", manifest).group(1)
    assert f"docker build -t {image} ." in readme
    assert f"kubectl create secret generic {secret} " in readme
    assert "kubectl apply -f kubernetes.yaml" in readme


def test_readme_documents_only_supported_targets():
    readme = readme_content("generated", [], GENERATED_APP_ENV_VARS).lower()
    for unsupported in ("helm", "terraform", "kustomize", "docker swarm", "ecs", "cloud run"):
        assert unsupported not in readme
