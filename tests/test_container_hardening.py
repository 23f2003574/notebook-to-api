"""Generated Kubernetes and Compose files run the app with a read-only root filesystem."""
import nbformat
import pytest
import yaml

from backend.compiler import compile_notebook


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("harden")
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell("def f(x: int) -> int:\n    return x\n"))
    nbformat.write(nb, tmp / "nb.ipynb")
    compile_notebook(str(tmp / "nb.ipynb"), str(tmp / "hardapp"))
    return tmp / "hardapp"


def _kubernetes(built):
    documents = list(yaml.safe_load_all((built / "kubernetes.yaml").read_text()))
    deployment = next(d for d in documents if d and d["kind"] == "Deployment")
    return deployment["spec"]["template"]["spec"]


def test_kubernetes_container_is_read_only_non_root_and_unprivileged(built):
    spec = _kubernetes(built)
    context = spec["containers"][0]["securityContext"]
    assert context["readOnlyRootFilesystem"] is True
    assert context["runAsNonRoot"] is True and context["runAsUser"] == 1000
    assert context["allowPrivilegeEscalation"] is False and context["capabilities"] == {"drop": ["ALL"]}


def test_kubernetes_pod_has_no_service_account_token_and_writable_data_and_tmp(built):
    spec = _kubernetes(built)
    assert spec["automountServiceAccountToken"] is False
    mounts = {m["name"]: m["mountPath"] for m in spec["containers"][0]["volumeMounts"]}
    assert mounts == {"tmp": "/tmp", "task-data": "/data"}
    assert {v["name"] for v in spec["volumes"]} == {"tmp", "task-data"}
    assert all("emptyDir" in v for v in spec["volumes"])


def test_kubernetes_probes_and_task_store_are_preserved(built):
    container = _kubernetes(built)["containers"][0]
    assert {"livenessProbe", "readinessProbe", "startupProbe"} <= set(container)
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["NOTEBOOK_API_TASK_STORE_PATH"] == "/data/tasks.json"


def test_compose_service_is_read_only_with_tmpfs_and_the_data_volume(built):
    text = (built / "docker-compose.yml").read_text()
    assert "read_only: ${NOTEBOOK_API_READ_ONLY_ROOTFS:-true}" in text
    service = next(iter(yaml.safe_load(text.replace("${NOTEBOOK_API_READ_ONLY_ROOTFS:-true}", "true"))["services"].values()))
    assert service["read_only"] is True and service["tmpfs"] == ["/tmp"]
    assert service["cap_drop"] == ["ALL"] and service["security_opt"] == ["no-new-privileges:true"]
    assert service["volumes"] == ["task-data:/data"]


def test_compose_read_only_can_be_switched_off_for_notebooks_that_write_elsewhere(built):
    text = (built / "docker-compose.yml").read_text()
    interpolated = text.replace("${NOTEBOOK_API_READ_ONLY_ROOTFS:-true}", "false")
    assert next(iter(yaml.safe_load(interpolated)["services"].values()))["read_only"] is False


def test_the_image_only_writes_where_the_manifests_provide_space(built):
    dockerfile = (built / "Dockerfile").read_text()
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile  # no .pyc writes into the read-only root
    assert "mkdir -p /data" in dockerfile and "VOLUME /data" in dockerfile
