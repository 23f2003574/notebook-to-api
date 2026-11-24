"""Generated Kubernetes and compose files carry seccomp, bounded scratch volumes, ephemeral-storage limits,
an optional NetworkPolicy and log rotation."""
import pytest
import yaml

from backend.generator.docker_generator import COMPOSE_ONLY_ENV_VARS, docker_compose_content, env_example_content
from backend.generator.kubernetes_generator import kubernetes_manifest_content

ENV = [{"name": "NOTEBOOK_API_TASK_STORE_PATH", "default": "/data/tasks.json", "description": "Where tasks are saved."}]


def _docs(monkeypatch, **env):
    for key in ("NOTEBOOK_API_K8S_TMP_SIZE_LIMIT", "NOTEBOOK_API_K8S_DATA_SIZE_LIMIT",
                "NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_REQUEST", "NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_LIMIT",
                "NOTEBOOK_API_K8S_NETWORK_POLICY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return list(yaml.safe_load_all(kubernetes_manifest_content("svc", ENV)))


def _container(docs):
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    return deployment["spec"]["template"]["spec"], deployment["spec"]["template"]["spec"]["containers"][0]


def test_defaults_add_seccomp_bounded_volumes_and_ephemeral_storage(monkeypatch):
    docs = _docs(monkeypatch)
    pod, container = _container(docs)
    sc = container["securityContext"]
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert sc["runAsNonRoot"] is True and sc["readOnlyRootFilesystem"] is True and sc["capabilities"] == {"drop": ["ALL"]}
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["tmp"]["emptyDir"] == {"sizeLimit": "256Mi"}
    assert volumes["task-data"]["emptyDir"] == {"sizeLimit": "1Gi"}
    resources = container["resources"]
    assert resources["requests"]["ephemeral-storage"] == "128Mi" and resources["limits"]["ephemeral-storage"] == "2Gi"
    assert resources["limits"]["memory"] == "1Gi"
    assert [d["kind"] for d in docs] == ["Deployment", "Service"]


def test_custom_values_and_optional_network_policy(monkeypatch):
    docs = _docs(monkeypatch, NOTEBOOK_API_K8S_TMP_SIZE_LIMIT="64Mi", NOTEBOOK_API_K8S_DATA_SIZE_LIMIT="5Gi",
                 NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_LIMIT="10Gi", NOTEBOOK_API_K8S_NETWORK_POLICY="true")
    pod, container = _container(docs)
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert volumes["tmp"]["emptyDir"]["sizeLimit"] == "64Mi" and volumes["task-data"]["emptyDir"]["sizeLimit"] == "5Gi"
    assert container["resources"]["limits"]["ephemeral-storage"] == "10Gi"
    policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
    assert policy["spec"]["policyTypes"] == ["Ingress"] and policy["spec"]["ingress"][0]["ports"][0]["port"] == 8000
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "svc"}


@pytest.mark.parametrize("name", ["NOTEBOOK_API_K8S_TMP_SIZE_LIMIT", "NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_LIMIT"])
@pytest.mark.parametrize("bad", ["lots", "-1Gi", "0", "10 GB"])
def test_invalid_quantities_are_rejected_at_compile_time(monkeypatch, name, bad):
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name):
        kubernetes_manifest_content("svc", ENV)


def test_compose_rotates_logs_with_configurable_limits():
    compose = yaml.safe_load(docker_compose_content("svc", ENV))
    logging = next(iter(compose["services"].values()))["logging"]
    assert logging["driver"] == "json-file"
    assert logging["options"] == {"max-size": "${NOTEBOOK_API_LOG_MAX_SIZE:-10m}", "max-file": "${NOTEBOOK_API_LOG_MAX_FILE:-3}"}
    assert {"NOTEBOOK_API_LOG_MAX_SIZE", "NOTEBOOK_API_LOG_MAX_FILE"} <= COMPOSE_ONLY_ENV_VARS
    example = env_example_content(ENV)
    assert "NOTEBOOK_API_LOG_MAX_SIZE=10m" in example and "NOTEBOOK_API_LOG_MAX_FILE=3" in example
