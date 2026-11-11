"""Structural validation of generated Compose and Kubernetes manifests.

Parses the YAML (PyYAML is imported lazily, so generation itself needs no extra
runtime dependency) and checks the fields `docker compose up` / `kubectl apply`
need. This is a structural check, not a substitute for those tools' own schema
validation.
"""
from __future__ import annotations


class ManifestValidationError(ValueError):
    """A generated manifest is not structurally valid; the message says where."""


def _load(text, label):
    try:
        import yaml
    except ImportError:
        raise ManifestValidationError("PyYAML is required to validate generated manifests") from None
    try:
        return list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        raise ManifestValidationError(f"{label} is not valid YAML: {exc}") from None


def _require(cond, message):
    if not cond:
        raise ManifestValidationError(message)


def validate_compose_manifest(text):
    docs = _load(text, "docker-compose.yml")
    _require(len(docs) == 1 and isinstance(docs[0], dict), "docker-compose.yml must be one YAML mapping")
    services = docs[0].get("services")
    _require(isinstance(services, dict) and services, "docker-compose.yml: 'services' must be a non-empty mapping")
    for name, svc in services.items():
        where = f"docker-compose.yml: service '{name}'"
        _require(isinstance(svc, dict), f"{where} must be a mapping")
        _require("build" in svc or "image" in svc, f"{where} needs 'build' or 'image'")
        env = svc.get("environment", [])
        _require(isinstance(env, (list, dict)), f"{where}: 'environment' must be a list or mapping")
        if isinstance(env, list):
            bad = [e for e in env if not isinstance(e, str) or not e.split("=", 1)[0]]
            _require(not bad, f"{where}: malformed environment entries {bad!r}")
        _require(isinstance(svc.get("ports", []), list), f"{where}: 'ports' must be a list")
    return docs[0]


def validate_kubernetes_manifest(text):
    docs = [d for d in _load(text, "kubernetes.yaml") if d is not None]
    for i, doc in enumerate(docs):
        _require(isinstance(doc, dict), f"kubernetes.yaml: document {i + 1} must be a mapping")
        for key in ("apiVersion", "kind", "metadata"):
            _require(key in doc, f"kubernetes.yaml: document {i + 1} is missing '{key}'")
        _require(doc["metadata"].get("name") if isinstance(doc["metadata"], dict) else False,
                 f"kubernetes.yaml: {doc['kind']} is missing metadata.name")
    kinds = {d["kind"]: d for d in docs}
    _require("Deployment" in kinds and "Service" in kinds, "kubernetes.yaml must define a Deployment and a Service")
    spec = kinds["Deployment"].get("spec") or {}
    labels = ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}
    containers = ((spec.get("template") or {}).get("spec") or {}).get("containers")
    _require(isinstance(containers, list) and containers, "kubernetes.yaml: Deployment has no containers")
    for c in containers:
        _require(c.get("name") and c.get("image"), "kubernetes.yaml: every container needs 'name' and 'image'")
        for e in c.get("env", []):
            _require("name" in e and ("value" in e) != ("valueFrom" in e),
                     f"kubernetes.yaml: env entry {e.get('name')!r} needs exactly one of value/valueFrom")
    selector = (kinds["Service"].get("spec") or {}).get("selector") or {}
    _require(selector and all(labels.get(k) == v for k, v in selector.items()),
             "kubernetes.yaml: Service selector does not match the Deployment pod labels")
    _require((kinds["Service"].get("spec") or {}).get("ports"), "kubernetes.yaml: Service defines no ports")
    return docs


REQUIRED_DEPLOY_ARTIFACTS = (
    "Dockerfile", ".dockerignore", "docker-compose.yml", ".env.example", "kubernetes.yaml", "README.md",
)


def verify_deployment_artifacts(stage_dir, output_dir):
    """Integrity check run on freshly generated artifacts before they replace
    the published ones: every expected file exists and is non-empty, the
    Dockerfile has FROM and CMD, and each file the Dockerfile COPYs by name
    (e.g. requirements.txt) exists in `output_dir`, where it is built from."""
    import os
    import shlex

    for name in REQUIRED_DEPLOY_ARTIFACTS:
        path = os.path.join(stage_dir, name)
        _require(os.path.isfile(path), f"{name}: expected deployment artifact was not generated")
        with open(path, encoding="utf-8") as f:
            _require(f.read().strip(), f"{name}: generated deployment artifact is empty")
    with open(os.path.join(stage_dir, "Dockerfile"), encoding="utf-8") as f:
        # Instructions start in column 0; indented "CMD" is a HEALTHCHECK's.
        lines = [ln.rstrip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    for instruction in ("FROM", "CMD"):
        _require(any(ln.upper().startswith(instruction + " ") for ln in lines),
                 f"Dockerfile: missing {instruction} instruction")
    for ln in lines:
        if not ln.upper().startswith("COPY "):
            continue
        parts = [p for p in shlex.split(ln)[1:] if not p.startswith("--")]
        for src in parts[:-1]:
            if src not in (".", "./") and not os.path.exists(os.path.join(output_dir, src)):
                raise ManifestValidationError(f"Dockerfile: COPY source {src!r} does not exist in the output directory")
