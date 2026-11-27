"""Opt-in PodDisruptionBudget, TLS Ingress and PersistentVolumeClaim in the generated manifest."""
import pytest
import yaml

from backend.generator.api_generator import GENERATED_APP_ENV_VARS
from backend.generator.kubernetes_generator import kubernetes_manifest_content

OPTIONS = ("NOTEBOOK_API_K8S_REPLICAS", "NOTEBOOK_API_K8S_PDB", "NOTEBOOK_API_K8S_PVC_SIZE", "NOTEBOOK_API_K8S_STORAGE_CLASS",
           "NOTEBOOK_API_K8S_INGRESS_HOST", "NOTEBOOK_API_K8S_INGRESS_TLS_SECRET", "NOTEBOOK_API_K8S_INGRESS_CLASS")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in OPTIONS:
        monkeypatch.delenv(name, raising=False)


def docs(**env):
    import os

    os.environ.update(env)
    try:
        text = kubernetes_manifest_content("My_App", env_vars=GENERATED_APP_ENV_VARS)
    finally:
        for name in env:
            os.environ.pop(name, None)
    return {d["kind"]: d for d in yaml.safe_load_all(text)}


def test_defaults_are_unchanged():
    assert set(docs()) == {"Deployment", "Service"}
    assert docs()["Deployment"]["spec"]["replicas"] == 1


def test_pod_disruption_budget_selects_the_deployment_pods():
    d = docs(NOTEBOOK_API_K8S_REPLICAS="3", NOTEBOOK_API_K8S_PDB="1")
    assert d["Deployment"]["spec"]["replicas"] == 3
    pdb = d["PodDisruptionBudget"]
    assert pdb["apiVersion"] == "policy/v1" and pdb["spec"]["minAvailable"] == 1
    assert pdb["spec"]["selector"]["matchLabels"] == d["Deployment"]["spec"]["selector"]["matchLabels"]


def test_ingress_is_tls_only_and_points_at_the_service():
    d = docs(NOTEBOOK_API_K8S_INGRESS_HOST="api.example.com", NOTEBOOK_API_K8S_INGRESS_TLS_SECRET="api-tls",
             NOTEBOOK_API_K8S_INGRESS_CLASS="nginx")
    spec = d["Ingress"]["spec"]
    assert spec["tls"] == [{"hosts": ["api.example.com"], "secretName": "api-tls"}]
    assert spec["ingressClassName"] == "nginx"
    backend = spec["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": d["Service"]["metadata"]["name"], "port": {"number": 80}}
    assert d["Service"]["spec"]["ports"][0]["port"] == 80


def test_persistent_claim_is_declared_mounted_and_writable():
    d = docs(NOTEBOOK_API_K8S_PVC_SIZE="2Gi", NOTEBOOK_API_K8S_STORAGE_CLASS="fast")
    pvc = d["PersistentVolumeClaim"]["spec"]
    assert pvc["resources"]["requests"]["storage"] == "2Gi" and pvc["storageClassName"] == "fast"
    pod = d["Deployment"]["spec"]["template"]["spec"]
    volume = next(v for v in pod["volumes"] if v["name"] == "task-data")
    assert volume["persistentVolumeClaim"]["claimName"] == d["PersistentVolumeClaim"]["metadata"]["name"]
    assert any(m["name"] == "task-data" for m in pod["containers"][0]["volumeMounts"])
    assert pod["securityContext"]["fsGroup"] == 1000
    assert "emptyDir" not in volume


@pytest.mark.parametrize("env", [
    {"NOTEBOOK_API_K8S_INGRESS_HOST": "api.example.com"},                       # no TLS secret
    {"NOTEBOOK_API_K8S_INGRESS_TLS_SECRET": "tls"},                             # no host
    {"NOTEBOOK_API_K8S_INGRESS_HOST": "Bad_Host", "NOTEBOOK_API_K8S_INGRESS_TLS_SECRET": "tls"},
    {"NOTEBOOK_API_K8S_PDB": "1"},                                              # one replica would block drains
    {"NOTEBOOK_API_K8S_REPLICAS": "0"},
    {"NOTEBOOK_API_K8S_REPLICAS": "many"},
    {"NOTEBOOK_API_K8S_PVC_SIZE": "lots"},
    {"NOTEBOOK_API_K8S_PVC_SIZE": "1Gi", "NOTEBOOK_API_K8S_REPLICAS": "2"},    # single-writer store
    {"NOTEBOOK_API_K8S_PVC_SIZE": "1Gi", "NOTEBOOK_API_K8S_STORAGE_CLASS": "Not Valid"},
])
def test_invalid_combinations_are_refused(env):
    with pytest.raises(ValueError):
        docs(**env)
