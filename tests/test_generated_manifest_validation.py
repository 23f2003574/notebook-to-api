import pytest

pytest.importorskip("yaml")

from backend.generator.api_generator import GENERATED_APP_ENV_VARS
from backend.generator.docker_generator import docker_compose_content
from backend.generator.kubernetes_generator import kubernetes_manifest_content
from backend.generator.manifest_validator import (
    ManifestValidationError, validate_compose_manifest, validate_kubernetes_manifest,
)

NB = [{"name": "OPENAI_KEY", "required": True}, {"name": "OPT", "required": False}]


@pytest.mark.parametrize("name", ["generated", "my_notebook_app", "café"])
def test_generated_manifests_are_valid(name):
    validate_compose_manifest(docker_compose_content(name, GENERATED_APP_ENV_VARS, NB))
    validate_kubernetes_manifest(kubernetes_manifest_content(name, GENERATED_APP_ENV_VARS, notebook_env_vars=NB))


@pytest.mark.parametrize("text,msg", [
    ("services: [", "not valid YAML"),
    ("version: '3'\n", "'services'"),
    ("services:\n  a:\n    restart: always\n", "'build' or 'image'"),
    ("services:\n  a:\n    build: .\n    environment: [1]\n", "malformed environment"),
])
def test_malformed_compose_is_rejected(text, msg):
    with pytest.raises(ManifestValidationError, match=msg):
        validate_compose_manifest(text)


def _k8s():
    return kubernetes_manifest_content(env_vars=GENERATED_APP_ENV_VARS)


@pytest.mark.parametrize("old,new,msg", [
    ("kind: Service", "kind: Svc", "Deployment and a Service"),
    ("apiVersion: apps/v1\n", "", "missing 'apiVersion'"),
    ("          image:", "          imag:", "'name' and 'image'"),
])
def test_malformed_kubernetes_is_rejected(old, new, msg):
    text = _k8s().replace(old, new, 1)
    with pytest.raises(ManifestValidationError, match=msg):
        validate_kubernetes_manifest(text)


def test_selector_mismatch_is_rejected():
    text = _k8s()
    idx = text.index("kind: Service")
    text = text[:idx] + text[idx:].replace("app:", "app: x-", 1)
    with pytest.raises(ManifestValidationError, match="selector"):
        validate_kubernetes_manifest(text)


def test_deployment_selector_must_match_template_labels():
    text = _k8s()
    idx = text.index("matchLabels:")
    text = text[:idx] + text[idx:].replace("app:", "app: other-", 1)
    with pytest.raises(ManifestValidationError, match="matchLabels"):
        validate_kubernetes_manifest(text)


def test_service_target_port_must_be_a_container_port():
    text = _k8s().replace("targetPort: 8000", "targetPort: 9999")
    with pytest.raises(ManifestValidationError, match="targetPort 9999"):
        validate_kubernetes_manifest(text)


def test_duplicate_environment_variables_are_rejected():
    with pytest.raises(ManifestValidationError, match="duplicate environment variables: A"):
        validate_compose_manifest("services:\n  a:\n    build: .\n    environment:\n      - A=1\n      - A=2\n")
    text = _k8s().replace("- name: PORT", "- name: NOTEBOOK_API_KEY\n              value: x\n            - name: PORT", 1)
    with pytest.raises(ManifestValidationError, match="duplicate env names: NOTEBOOK_API_KEY"):
        validate_kubernetes_manifest(text)
