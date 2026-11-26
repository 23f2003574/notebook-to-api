"""The dashboard's Kubernetes file is usable as generated: reachable and locked down."""
import yaml

from backend.generator.dashboard_deployment import KUBERNETES


def _docs():
    return {(d["kind"]): d for d in yaml.safe_load_all(KUBERNETES)}


def test_file_holds_pvc_deployment_service_and_network_policy():
    assert set(_docs()) == {"PersistentVolumeClaim", "Deployment", "Service", "NetworkPolicy"}


def test_service_selects_the_deployment_pods_and_the_container_port():
    docs = _docs()
    labels = docs["Deployment"]["spec"]["template"]["metadata"]["labels"]
    service = docs["Service"]["spec"]
    assert service["selector"] == labels
    container_port = docs["Deployment"]["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"]
    assert service["ports"][0]["targetPort"] == container_port == 8001


def test_network_policy_admits_only_the_dashboard_port_to_the_dashboard_pods():
    docs = _docs()
    policy = docs["NetworkPolicy"]["spec"]
    assert policy["podSelector"]["matchLabels"] == docs["Deployment"]["spec"]["template"]["metadata"]["labels"]
    assert policy["policyTypes"] == ["Ingress"]
    assert [(p["protocol"], p["port"]) for rule in policy["ingress"] for p in rule["ports"]] == [("TCP", 8001)]


def test_service_account_token_is_not_mounted():
    pod = _docs()["Deployment"]["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False


def test_readme_documents_the_new_resources():
    from backend.generator.dashboard_deployment import README
    assert "NetworkPolicy" in README and "Service" in README
