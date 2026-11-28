"""Opt-in HorizontalPodAutoscaler and topology spread constraints in the generated manifest."""
import pytest

from tests.test_kubernetes_optional_resources import clean, docs  # noqa: F401 - clean is an autouse fixture

pytestmark = pytest.mark.usefixtures("clean")


def test_nothing_is_generated_unless_asked_for():
    d = docs()
    assert "HorizontalPodAutoscaler" not in d and d["Deployment"]["spec"]["replicas"] == 1
    assert "topologySpreadConstraints" not in d["Deployment"]["spec"]["template"]["spec"]


def test_hpa_targets_the_deployment_cpu_request_and_leaves_the_count_to_the_autoscaler():
    d = docs(NOTEBOOK_API_K8S_REPLICAS="2", NOTEBOOK_API_K8S_HPA_MAX_REPLICAS="8", NOTEBOOK_API_K8S_HPA_CPU_UTILIZATION="60")
    hpa, deployment = d["HorizontalPodAutoscaler"], d["Deployment"]
    assert hpa["spec"]["scaleTargetRef"] == {"apiVersion": "apps/v1", "kind": "Deployment", "name": deployment["metadata"]["name"]}
    assert (hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]) == (2, 8)
    assert hpa["spec"]["metrics"][0]["resource"] == {"name": "cpu", "target": {"type": "Utilization", "averageUtilization": 60}}
    assert "replicas" not in deployment["spec"]  # otherwise every apply would reset the autoscaler's count
    assert "cpu" in deployment["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]


@pytest.mark.parametrize("env,message", [
    ({"NOTEBOOK_API_K8S_HPA_MAX_REPLICAS": "1"}, "from 2 to 100"),
    ({"NOTEBOOK_API_K8S_HPA_MAX_REPLICAS": "many"}, "from 2 to 100"),
    ({"NOTEBOOK_API_K8S_REPLICAS": "5", "NOTEBOOK_API_K8S_HPA_MAX_REPLICAS": "3"}, "below"),
    ({"NOTEBOOK_API_K8S_HPA_MAX_REPLICAS": "4", "NOTEBOOK_API_K8S_HPA_CPU_UTILIZATION": "0"}, "1 to 100"),
    ({"NOTEBOOK_API_K8S_HPA_MAX_REPLICAS": "4", "NOTEBOOK_API_K8S_PVC_SIZE": "1Gi"}, "single-writer"),
    ({"NOTEBOOK_API_K8S_HPA_MAX_REPLICAS": "4", "NOTEBOOK_API_K8S_PDB": "1"}, "at least 2"),
    ({"NOTEBOOK_API_K8S_TOPOLOGY_SPREAD": "rack", "NOTEBOOK_API_K8S_REPLICAS": "2"}, "node, zone or both"),
    ({"NOTEBOOK_API_K8S_TOPOLOGY_SPREAD": "node"}, "at least 2 replicas"),
])
def test_impossible_combinations_are_refused(env, message):
    with pytest.raises(ValueError, match=message):
        docs(**env)


def test_hpa_with_a_pdb_is_valid_when_the_minimum_keeps_two_pods():
    d = docs(NOTEBOOK_API_K8S_REPLICAS="2", NOTEBOOK_API_K8S_PDB="1", NOTEBOOK_API_K8S_HPA_MAX_REPLICAS="4")
    assert d["PodDisruptionBudget"]["spec"]["minAvailable"] == 1 < d["HorizontalPodAutoscaler"]["spec"]["minReplicas"]


def test_topology_spread_selects_the_apps_pods_and_is_soft_unless_strict():
    spec = docs(NOTEBOOK_API_K8S_REPLICAS="3", NOTEBOOK_API_K8S_TOPOLOGY_SPREAD="both")["Deployment"]["spec"]
    constraints = spec["template"]["spec"]["topologySpreadConstraints"]
    assert [c["topologyKey"] for c in constraints] == ["topology.kubernetes.io/zone", "kubernetes.io/hostname"]
    assert {c["whenUnsatisfiable"] for c in constraints} == {"ScheduleAnyway"}
    for c in constraints:
        assert c["labelSelector"]["matchLabels"] == spec["selector"]["matchLabels"] == spec["template"]["metadata"]["labels"]
    strict = docs(NOTEBOOK_API_K8S_HPA_MAX_REPLICAS="4", NOTEBOOK_API_K8S_TOPOLOGY_SPREAD="node",
                  NOTEBOOK_API_K8S_TOPOLOGY_SPREAD_STRICT="1")["Deployment"]["spec"]["template"]["spec"]
    assert strict["topologySpreadConstraints"][0]["whenUnsatisfiable"] == "DoNotSchedule"  # max replicas count for the check
