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


def _k8s_with(env_mb=None, limits=None, requests=None):
    import yaml

    docs = list(yaml.safe_load_all(kubernetes_manifest_content("app", GENERATED_APP_ENV_VARS, notebook_env_vars=NB)))
    container = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]["containers"][0]
    if env_mb is not None:
        for e in container["env"]:
            if e["name"] == "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB":
                e["value"] = env_mb
    if limits is not None:
        container["resources"]["limits"] = limits
    if requests is not None:
        container["resources"]["requests"] = requests
    return yaml.safe_dump_all(docs)


def test_matching_memory_limits_pass():
    validate_kubernetes_manifest(_k8s_with(env_mb="512"))
    validate_kubernetes_manifest(_k8s_with(env_mb="1024"))  # equal to the 1Gi limit


def test_unspecified_limits_are_not_compared():
    validate_kubernetes_manifest(_k8s_with())  # default "0": no per-call limit
    validate_kubernetes_manifest(_k8s_with(env_mb="4096", limits={}))  # no container memory limit


def test_isolated_memory_limit_above_container_limit_is_rejected():
    with pytest.raises(ManifestValidationError, match="ISOLATE_MEMORY_LIMIT_MB=2048 exceeds the container memory limit 1Gi"):
        validate_kubernetes_manifest(_k8s_with(env_mb="2048"))


def test_requests_above_limits_are_rejected_for_memory_and_cpu():
    with pytest.raises(ManifestValidationError, match="requests.memory 2Gi exceeds limits.memory 1Gi"):
        validate_kubernetes_manifest(_k8s_with(requests={"cpu": "100m", "memory": "2Gi"}))
    with pytest.raises(ManifestValidationError, match="requests.cpu 2 exceeds limits.cpu 500m"):
        validate_kubernetes_manifest(_k8s_with(limits={"memory": "1Gi", "cpu": "500m"}, requests={"cpu": "2", "memory": "256Mi"}))


def test_invalid_resource_representations_are_rejected():
    with pytest.raises(ManifestValidationError, match="not a valid Kubernetes resource quantity"):
        validate_kubernetes_manifest(_k8s_with(limits={"memory": "lots"}))
    with pytest.raises(ManifestValidationError, match="not a whole number of MB"):
        validate_kubernetes_manifest(_k8s_with(env_mb="1.5g"))
