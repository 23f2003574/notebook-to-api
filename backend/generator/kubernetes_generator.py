import re

from backend.exporters.openapi_exporter import _yaml_scalar
from backend.generator.docker_generator import (
    TASK_STORE_DIR, TASK_STORE_FILE, TASK_STORE_VAR, default_image_tag,
)

# Kubernetes' own DNS-1123 label rule for a resource's "metadata.name" (and,
# identically, a label value): lowercase alphanumeric or '-', must start and
# end with an alphanumeric character, max 63 characters. `package_name` only
# has to satisfy Python's own `str.isidentifier()` (package_name_for_output_dir,
# backend/compiler.py) -- which happily allows uppercase letters and
# underscores, *neither* of which are legal here. Confirmed exploitable: a
# perfectly ordinary `--output my_notebook_app` (underscores are completely
# idiomatic in a Python package name) compiled without error, but
# `kubectl apply -f kubernetes.yaml` against the resulting manifest failed
# outright with "metadata.name: Invalid value: \"my_notebook_app\": ... a
# lowercase RFC 1123 subdomain must consist of lower case alphanumeric
# characters, '-' or '.'" -- the one artifact this function exists to let an
# operator apply with no further editing (see this module's own "the minimal
# pair `kubectl apply -f` needs" below) was actually invalid the moment the
# compiled package name contained an underscore or a capital letter, with
# nothing about the compile itself ever warning of it.
_K8S_NAME_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")

# Compile-time hardening settings, read from the environment of the process that compiles. Each value is
# validated: a malformed Kubernetes quantity would otherwise only fail at `kubectl apply`.
_QUANTITY_RE = re.compile(r"^[1-9][0-9]*(\.[0-9]+)?(Ki|Mi|Gi|Ti|k|M|G|T)?$")
_K8S_SETTINGS = {
    "NOTEBOOK_API_K8S_TMP_SIZE_LIMIT": "256Mi",
    "NOTEBOOK_API_K8S_DATA_SIZE_LIMIT": "1Gi",
    "NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_REQUEST": "128Mi",
    "NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_LIMIT": "2Gi",
}


def _k8s_quantity(name, environ=None):
    import os

    value = (os.environ if environ is None else environ).get(name, "").strip() or _K8S_SETTINGS[name]

    if not _QUANTITY_RE.match(value):
        raise ValueError(f"{name}={value!r} is not a valid Kubernetes quantity (for example 256Mi or 1Gi)")

    return value


def _k8s_network_policy_enabled(environ=None):
    import os

    return (os.environ if environ is None else environ).get(
        "NOTEBOOK_API_K8S_NETWORK_POLICY", ""
    ).strip().lower() in {"1", "true", "yes"}


_METADATA_IP = "169.254.169.254"


def _k8s_egress_rules(environ=None):
    """Egress rules for NOTEBOOK_API_K8S_EGRESS_CIDRS, or None when the variable is unset.

    "none" (or an empty list) allows DNS only; otherwise each comma-separated CIDR is allowed in
    addition to DNS. A CIDR that contains the cloud metadata address gets it carved out via
    `except`. Raises ValueError for a malformed CIDR, or when set without the network policy.
    """
    import ipaddress
    import os

    environ = os.environ if environ is None else environ
    raw = environ.get("NOTEBOOK_API_K8S_EGRESS_CIDRS")
    if raw is None or not raw.strip():
        return None
    if not _k8s_network_policy_enabled(environ):
        raise ValueError(
            "NOTEBOOK_API_K8S_EGRESS_CIDRS has no effect unless NOTEBOOK_API_K8S_NETWORK_POLICY=true"
        )
    rules = [{"ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]}]
    metadata = ipaddress.ip_address(_METADATA_IP)
    for item in raw.split(","):
        item = item.strip()
        if not item or item.lower() == "none":
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise ValueError(
                f"NOTEBOOK_API_K8S_EGRESS_CIDRS entry {item!r} is not a valid CIDR (use e.g. 203.0.113.0/24)"
            ) from None
        block = {"cidr": str(network)}
        if network.version == 4 and metadata in network and network.prefixlen < 32:
            block["except"] = [f"{_METADATA_IP}/32"]
        elif network.version == 4 and metadata in network:
            raise ValueError(f"NOTEBOOK_API_K8S_EGRESS_CIDRS must not allow the cloud metadata address {_METADATA_IP}")
        rules.append({"to": [{"ipBlock": block}]})
    return rules


def _k8s_resource_name(package_name):
    """A DNS-1123-legal resource name derived from `package_name` (see the
    module-level comment above for exactly which names this fixes).

    Lowercases the name, then replaces any run of characters other than
    `[a-z0-9-]` (an underscore chief among them, for a real package name)
    with a single '-', so "My_App" becomes "my-app" rather than either an
    invalid literal pass-through or a value that collapses two distinct
    package names to the same manifest name (e.g. "my__app" and "my_app"
    both becoming "my-app" is an acceptable, documented trade-off here --
    still deterministic and still far better than an invalid manifest).
    Leading/trailing '-' (which a name starting or ending with an
    underscore would otherwise leave behind, itself still illegal here) is
    then stripped, and the result is truncated to Kubernetes' own 63-
    character limit -- re-stripping any '-' the truncation itself exposed
    at the new end.

    Falls back to the literal "generated" (the same default `package_name`
    already uses elsewhere in this module) on the one input that survives
    all of the above as an empty string: a name made up entirely of
    characters this regex strips (e.g. "___") -- an empty "metadata.name"
    is its own, differently-worded Kubernetes validation error, not
    something a real `--output` value would plausibly produce, but not
    reachable here now either way.
    """
    name = _K8S_NAME_INVALID_CHARS_RE.sub("-", package_name.lower()).strip("-")
    return name[:63].strip("-") or "generated"


_DNS_SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")


def _k8s_replicas(environ=None):
    """NOTEBOOK_API_K8S_REPLICAS (default 1, at most 50)."""
    import os

    raw = (os.environ if environ is None else environ).get("NOTEBOOK_API_K8S_REPLICAS", "").strip() or "1"
    if not raw.isdigit() or not 1 <= int(raw) <= 50:
        raise ValueError(f"NOTEBOOK_API_K8S_REPLICAS={raw!r} must be a whole number from 1 to 50")
    return int(raw)


def _k8s_dns_name(name, value, limit=253):
    if len(value) > limit or not _DNS_SUBDOMAIN_RE.match(value):
        raise ValueError(f"{name}={value!r} is not a valid Kubernetes DNS name")
    return value


def _k8s_optional_resources(resource_name, environ=None):
    """The opt-in resources and the settings they imply, validated together:
    (replicas, pvc_yaml or "", pvc_claim or None, extra_yaml). Nothing is generated unless asked for;
    an Ingress is only produced with an explicit host AND a TLS secret, never as plain HTTP."""
    import os

    env = os.environ if environ is None else environ
    replicas = _k8s_replicas(env)
    extra = ""
    pvc_size = env.get("NOTEBOOK_API_K8S_PVC_SIZE", "").strip()
    pvc_yaml, claim = "", None
    if pvc_size:
        if not _QUANTITY_RE.match(pvc_size):
            raise ValueError(f"NOTEBOOK_API_K8S_PVC_SIZE={pvc_size!r} is not a valid Kubernetes quantity (for example 1Gi)")
        if replicas > 1:
            raise ValueError("NOTEBOOK_API_K8S_PVC_SIZE needs NOTEBOOK_API_K8S_REPLICAS=1: the task store is a "
                             "single-writer file on a ReadWriteOnce volume")
        storage_class = env.get("NOTEBOOK_API_K8S_STORAGE_CLASS", "").strip()
        class_line = f"  storageClassName: {_k8s_dns_name('NOTEBOOK_API_K8S_STORAGE_CLASS', storage_class)}\n" if storage_class else ""
        claim = f"{resource_name}-data"
        pvc_yaml = (
            "---\napiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n"
            f"  name: {claim}\n  labels:\n    app: {resource_name}\nspec:\n"
            f"  accessModes: [ReadWriteOnce]\n{class_line}  resources:\n    requests:\n      storage: {pvc_size}\n"
        )
        extra += pvc_yaml
    if env.get("NOTEBOOK_API_K8S_PDB", "").strip().lower() in {"1", "true", "yes"}:
        if replicas < 2:
            raise ValueError("NOTEBOOK_API_K8S_PDB needs NOTEBOOK_API_K8S_REPLICAS of at least 2: with one replica "
                             "a minAvailable budget would block every node drain")
        extra += (
            "---\napiVersion: policy/v1\nkind: PodDisruptionBudget\nmetadata:\n"
            f"  name: {resource_name}\nspec:\n  minAvailable: 1\n  selector:\n    matchLabels:\n"
            f"      app: {resource_name}\n"
        )
    host = env.get("NOTEBOOK_API_K8S_INGRESS_HOST", "").strip()
    tls_secret = env.get("NOTEBOOK_API_K8S_INGRESS_TLS_SECRET", "").strip()
    if host or tls_secret:
        if not (host and tls_secret):
            raise ValueError("an Ingress needs both NOTEBOOK_API_K8S_INGRESS_HOST and NOTEBOOK_API_K8S_INGRESS_TLS_SECRET; "
                             "this generator never produces a plain-HTTP Ingress")
        _k8s_dns_name("NOTEBOOK_API_K8S_INGRESS_HOST", host)
        _k8s_dns_name("NOTEBOOK_API_K8S_INGRESS_TLS_SECRET", tls_secret)
        ingress_class = env.get("NOTEBOOK_API_K8S_INGRESS_CLASS", "").strip()
        class_line = f"  ingressClassName: {_k8s_dns_name('NOTEBOOK_API_K8S_INGRESS_CLASS', ingress_class)}\n" if ingress_class else ""
        extra += (
            "---\napiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n"
            f"  name: {resource_name}\nspec:\n{class_line}  tls:\n    - hosts: [{host}]\n      secretName: {tls_secret}\n"
            f"  rules:\n    - host: {host}\n      http:\n        paths:\n          - path: /\n            pathType: Prefix\n"
            f"            backend:\n              service:\n                name: {resource_name}\n"
            "                port:\n                  number: 80\n"
        )
    return replicas, claim, extra


def kubernetes_manifest_content(
    package_name="generated", env_vars=None, image=None, notebook_env_vars=None,
):
    """The exact kubernetes.yaml text generate_kubernetes_manifest (below)
    writes to disk, as a pure string -- no filesystem access at all. See
    dockerfile_content's own docstring (backend/generator/docker_generator.py)
    for why this split exists.

    A compiled app already gets a Dockerfile, a docker-compose.yml for a
    single-host `docker compose up`, a .env.example, and a README -- but
    nothing for the far more common real deployment target those docstrings
    already name in passing (GET /api/env-vars-preview's own docstring: "An
    operator writing a docker-compose.yml, a Kubernetes manifest, or a plain
    .env file... had no way to discover any of this except by trial and
    error"): a Kubernetes cluster. An operator deploying there had to
    hand-write a Deployment/Service manifest from scratch, transcribing the
    Dockerfile's own $PORT/HEALTHCHECK contract and every NOTEBOOK_API_*
    variable GET /api/env-vars-preview already reports one field at a time,
    with nothing to catch a typo'd name, a stale default, or a probe path
    that's drifted from the app's own actual health/readiness routes.

    `env_vars` is GENERATED_APP_ENV_VARS (backend/generator/
    api_generator.py) -- the exact same list docker_compose_content/
    env_example_content already take (see docker_compose_content's own
    docstring for why it's passed in rather than imported directly here, to
    avoid a circular import) -- so the container's own "env:" list can
    never list a variable the compiled app doesn't actually recognize, or a
    default that's drifted from what it would actually fall back to.

    Each entry becomes a literal "name: value" pair rather than a
    shell-style "${NAME:-default}" the way docker_compose_content's own
    "environment:" section does -- Kubernetes env values aren't expanded by
    a shell at all, so there's no equivalent syntax; an operator overrides
    one by editing this manifest directly (or layering a Kustomize/Helm
    values file over it), the same "a real, valid file on its own, only a
    value you actually want to override needs editing" precedent
    env_example_content's own docstring already sets. NOTEBOOK_API_KEY in
    particular ships here as a plain literal for the same reason
    env_example_content leaves it as one: every value already matches the
    compiled app's own real default -- moving it into a Secret is a real
    deployment's job, not this preview's.

    "PORT" -- read by the Dockerfile's own CMD/HEALTHCHECK, never by the
    compiled app itself (see GET /api/env-vars-preview's own docstring for
    why it's deliberately excluded from GENERATED_APP_ENV_VARS) -- gets the
    identical unconditional inclusion docker_compose_content's own
    "environment:" section and env_example_content's own file already give
    it, driving the container's own containerPort/probe ports so they can
    never drift from what the container itself actually binds to.

    livenessProbe/readinessProbe target the compiled app's own GET
    /health/GET /ready -- neither requires an X-API-Key header (see
    RESERVED_INFRASTRUCTURE_NAMES in api_generator.py: health_check/
    readiness_check are the only two built-in routes with no
    Depends(verify_api_key)), the same unauthenticated endpoints the
    Dockerfile's own HEALTHCHECK already curls, so a probe here needs no
    credential this manifest would otherwise have to embed.

    Renders as two YAML documents separated by "---" (a Deployment, then a
    ClusterIP Service) -- the minimal pair `kubectl apply -f` needs to
    actually run and reach the compiled app inside the cluster; anything
    beyond that (an Ingress, a HorizontalPodAutoscaler, resource
    requests/limits) is a real deployment's own decision this tool has no
    way to make on an operator's behalf.

    `image` (optional) is the container's own "image:" reference --
    defaults to "{package_name.lower()}:latest" exactly as before this
    parameter existed (module-level comment below aside -- see there for
    why the default alone is lowercased), but a real cluster can't
    `docker build` on an operator's behalf the way a local `docker
    compose up` effectively can: it can only ever pull an already-pushed
    image by its exact tag. Before this, the hardcoded default was the
    *only* value this manifest could ever contain, silently wrong the
    moment a caller actually deployed under any other tag (POST
    /api/deploy's own "tag", or the CLI's `deploy --tag`) --
    `kubectl apply -f` against this manifest would then either pull an
    unrelated ":latest" image nothing just built, or fail outright
    against a registry that was never pushed to at all. Passing the real
    tag here is what actually closes that gap; see POST /api/deploy,
    which now does exactly that for the copy it writes to GENERATED_DIR
    after every successful build. A caller-supplied `image` is used
    exactly as given, uppercase and all -- only the *default*, derived
    from `package_name`, is ever lowercased.

    Every interpolated value below (`image`, `package_name`, and each
    env entry's own "name"/"default") is rendered through _yaml_scalar
    (backend/exporters/openapi_exporter.py) -- the same minimal, quoting-
    aware scalar renderer GET /api/export-openapi's own YAML format
    already uses, reused here rather than reimplemented a second time.
    Before this, `image` in particular was interpolated as a raw,
    unquoted f-string straight into a YAML value position -- and `image`
    is caller-controlled, byte-for-byte, via POST /api/deploy's own "tag"
    (written here on every successful build) and GET /api/k8s-preview's
    own "image" query param (returned directly in that response).
    Confirmed exploitable: a "tag" containing an embedded newline
    followed by more YAML (e.g. "myimage:latest\\n          command:
    [\\"sh\\", \\"-c\\", \\"...\\"]") didn't just fail to parse -- it
    successfully injected an entirely new, attacker-chosen key into this
    exact container's own spec, one indentation level below "image:",
    with nothing in this function ever noticing the value it was handed
    contained a line break at all. A YAML manifest is exactly the kind of
    artifact this project's own docstrings elsewhere assume gets
    `kubectl apply -f`'d with no further review (see this function's own
    "the minimal pair `kubectl apply -f` needs" above) -- silently
    injecting arbitrary keys into a real Deployment spec this way can
    reach `securityContext`, `command`, `volumeMounts`, or anything else
    a real Pod spec accepts. _yaml_scalar quotes (and properly escapes)
    any value that isn't safe to emit bare, the identical protection
    GET /api/export-openapi's own YAML format already gives every OpenAPI
    schema string for the identical reason.

    `package_name` -- valid everywhere else it's used, including as this
    manifest's own container/module name inside the Dockerfile's `COPY .
    {package_name}/` -- is additionally passed through _k8s_resource_name
    (above) everywhere it becomes a "metadata.name" or label value, since
    Kubernetes' own DNS-1123 naming rule (lowercase alphanumeric or '-'
    only) is stricter than the plain `str.isidentifier()` check
    package_name_for_output_dir (backend/compiler.py) already enforces --
    see that helper's own docstring for the exact underscore/uppercase
    failure this closes. A caller-*supplied* `image` is used exactly as
    given, uppercase and all: it's a registry reference already handled
    (and quoted) on its own terms above, not a name this function derives
    from `package_name` itself. Only the *default* -- "{package_name}:
    latest", built from `package_name` when no `image` is given at all --
    is lowercased first: a Docker image repository name must itself be
    all-lowercase, and `package_name` -- again, only ever validated as a
    plain Python identifier -- can just as easily carry an uppercase
    letter as it can the underscore _k8s_resource_name above already has
    to handle. Confirmed exploitable before this: a compiled package
    named "MyNotebookApp" baked "MyNotebookApp:latest" into this
    manifest's own "image:" by default, a reference `docker build`/
    `docker pull` themselves reject outright ("invalid reference format:
    repository name must be lowercase"). POST /api/deploy's own tag
    default (`generated_path.name.lower()`, routes/upload.py) already
    lowercases for exactly this reason -- this just brings the *plain-
    compile* default (no explicit deploy at all) in line with it, rather
    than leaving this one path still baking in an invalid reference.
    """
    env_vars = env_vars or []
    image = image or default_image_tag(package_name)

    package_name_scalar = _yaml_scalar(_k8s_resource_name(package_name))
    image_scalar = _yaml_scalar(image)

    env_entries = [{"name": "PORT", "default": "8000"}] + list(env_vars)
    resource_name = _k8s_resource_name(package_name)
    replicas, pvc_claim, optional_resources = _k8s_optional_resources(resource_name)
    secret_name = _yaml_scalar(f"{resource_name}-secrets")

    def env_block(entry):
        name = _yaml_scalar(entry["name"])
        # The API key is a credential: shipping the app's public default as a
        # literal in a manifest that gets `kubectl apply`'d left every cluster
        # deployment callable by anyone who knows this project's source. It is
        # read from a Secret instead (the pod won't start until one exists),
        # and REQUIRE_CUSTOM_KEY makes the app itself refuse the default.
        if entry["name"] == "NOTEBOOK_API_KEY":
            return (
                f"            - name: {name}\n"
                f"              valueFrom:\n"
                f"                secretKeyRef:\n"
                f"                  name: {secret_name}\n"
                f"                  key: NOTEBOOK_API_KEY"
            )
        # The webhook signing secret is a credential too: as a plain `value:`
        # it would be typed into a manifest that lives in version control.
        # Optional, since an empty/absent secret just sends webhooks unsigned.
        if entry["name"] == "NOTEBOOK_API_WEBHOOK_SECRET":
            return (
                f"            - name: {name}\n"
                f"              valueFrom:\n"
                f"                secretKeyRef:\n"
                f"                  name: {secret_name}\n"
                f"                  key: NOTEBOOK_API_WEBHOOK_SECRET\n"
                f"                  optional: true"
            )
        # A cluster is a production target: besides refusing the dev key, keep
        # notebook exception text (paths, tokens in messages) out of 500
        # responses; the full message still goes to the pod log.
        hardened = ("NOTEBOOK_API_REQUIRE_CUSTOM_KEY", "NOTEBOOK_API_HIDE_ERROR_DETAILS")
        value = "true" if entry["name"] in hardened else entry["default"]
        if entry["name"] == TASK_STORE_VAR:
            value = TASK_STORE_FILE
        # The app's http://localhost:8000 default is only right on a dev
        # machine; in a cluster, /docs' "Try it out" should hit the Service
        # this same manifest defines (port 80 -> the container's 8000).
        if entry["name"] == "NOTEBOOK_API_PUBLIC_URL" and value == "http://localhost:8000":
            value = f"http://{resource_name}"
        return (
            f"            - name: {name}\n"
            f"              value: {_yaml_scalar(value)}"
        )

    env_lines = "\n".join(env_block(entry) for entry in env_entries)

    # Task persistence: an emptyDir keeps the task file across container restarts within
    # the same Pod, but NOT across Pod replacement (rollout, eviction, node loss). Swap it
    # for a PersistentVolumeClaim if tasks must survive that. It is world-writable, so the
    # runAsUser 1000 container can write it.
    tmp_limit = _k8s_quantity("NOTEBOOK_API_K8S_TMP_SIZE_LIMIT")
    data_limit = _k8s_quantity("NOTEBOOK_API_K8S_DATA_SIZE_LIMIT")
    ephemeral_request = _k8s_quantity("NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_REQUEST")
    ephemeral_limit = _k8s_quantity("NOTEBOOK_API_K8S_EPHEMERAL_STORAGE_LIMIT")
    network_policy = ""

    egress_rules = _k8s_egress_rules()
    egress_yaml = ""
    if egress_rules is not None:
        import json

        egress_yaml = f"  egress: {json.dumps(egress_rules)}\n"
    if _k8s_network_policy_enabled():
        network_policy = f"""---
# Only inbound traffic to the app port is allowed. Egress is left open unless
# NOTEBOOK_API_K8S_EGRESS_CIDRS is set: then it is denied except DNS and the listed CIDRs (the cloud
# metadata address is never allowed). Enforced only by a CNI that implements NetworkPolicy.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {package_name_scalar}
spec:
  podSelector:
    matchLabels:
      app: {package_name_scalar}
  policyTypes: {'["Ingress", "Egress"]' if egress_rules is not None else '["Ingress"]'}
  ingress:
    - ports:
        - protocol: TCP
          port: 8000
{egress_yaml}"""

    volume_mount_lines = (
        "          volumeMounts:\n"
        "            - name: tmp\n"
        "              mountPath: /tmp\n"
    )
    volumes_lines = (
        "      volumes:\n"
        "        - name: tmp\n"
        f"          emptyDir: {{sizeLimit: {tmp_limit}}}\n"
    )
    if any(entry["name"] == TASK_STORE_VAR for entry in env_entries):
        volume_mount_lines += (
            "            - name: task-data\n"
            f"              mountPath: {TASK_STORE_DIR}\n"
        )
        volumes_lines = (
            "      # task-data (emptyDir): survives container restarts in this Pod, not Pod replacement\n"
            "      # (rollout, eviction, node loss). Use a PersistentVolumeClaim for that.\n"
            "      # tmp (emptyDir): the only other writable path, since the root filesystem is read-only.\n"
            "      volumes:\n"
            "        - name: tmp\n"
            f"          emptyDir: {{sizeLimit: {tmp_limit}}}\n"
            "        - name: task-data\n"
            f"          emptyDir: {{sizeLimit: {data_limit}}}\n"
        )
        if pvc_claim:
            volumes_lines = (
                "      # task-data: a PersistentVolumeClaim, so tasks survive Pod replacement.\n"
                "      volumes:\n"
                "        - name: tmp\n"
                f"          emptyDir: {{sizeLimit: {tmp_limit}}}\n"
                "        - name: task-data\n"
                "          persistentVolumeClaim:\n"
                f"            claimName: {pvc_claim}\n"
            )
    elif pvc_claim:
        raise ValueError("NOTEBOOK_API_K8S_PVC_SIZE has nothing to persist: this app does not use a task store")
    pod_security = "      securityContext:\n        fsGroup: 1000  # makes the claim writable for the non-root user\n" if pvc_claim else ""

    # Variables the notebook itself reads (API keys, connection strings) come
    # from the same Secret. A required one (no fallback in the notebook) is
    # mandatory -- the pod fails to start with a clear "key not found" until
    # it exists -- while an optional one is simply left unset when missing.
    notebook_env_vars = list(notebook_env_vars or [])
    for entry in notebook_env_vars:
        env_lines += (
            f"\n            - name: {_yaml_scalar(entry['name'])}\n"
            f"              valueFrom:\n"
            f"                secretKeyRef:\n"
            f"                  name: {secret_name}\n"
            f"                  key: {_yaml_scalar(entry['name'])}"
        )
        if not entry["required"]:
            env_lines += "\n                  optional: true"

    secret_comment = "".join(
        f" \\\n#     --from-literal={entry['name']}=<value>"
        for entry in notebook_env_vars if entry["required"]
    )
    optional_names = [entry["name"] for entry in notebook_env_vars if not entry["required"]]
    optional_comment = (
        "\n# Optional keys the notebook reads (add with --from-literal if needed): "
        + ", ".join(optional_names)
        if optional_names else ""
    )

    return f"""\
# NOTEBOOK_API_KEY comes from a Secret. Create it before applying this file:
#   kubectl create secret generic {resource_name}-secrets \\
#     --from-literal=NOTEBOOK_API_KEY="$(openssl rand -hex 32)"{secret_comment}{optional_comment}
# Optionally add NOTEBOOK_API_WEBHOOK_SECRET to the same Secret to sign webhooks.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {package_name_scalar}
  labels:
    app: {package_name_scalar}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {package_name_scalar}
  template:
    metadata:
      labels:
        app: {package_name_scalar}
    spec:
      # SIGTERM reaches the pod at the same moment its endpoint is removed
      # from the Service, and removal takes a few seconds to propagate -- so
      # without the preStop pause below, a rollout sends new requests to a
      # server that is already shutting down (connection resets). The grace
      # period must cover that pause plus the time to drain in-flight calls.
      terminationGracePeriodSeconds: 45
      # The app never calls the Kubernetes API, so the pod gets no service-account token.
      automountServiceAccountToken: false
{pod_security}      containers:
        - name: {package_name_scalar}
          image: {image_scalar}
          lifecycle:
            preStop:
              exec:
                command: ["sleep", "5"]
          ports:
            - containerPort: 8000
          # The Dockerfile's `USER appuser` is a name, which the kubelet
          # cannot verify as non-root, so runAsNonRoot would refuse to start
          # the pod without the explicit numeric uid the Dockerfile creates.
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            allowPrivilegeEscalation: false
            seccompProfile:
              type: RuntimeDefault
            # The image writes only to /data (task store) and /tmp, both mounted
            # below. Set this to false if the notebook itself writes elsewhere
            # in the container (a model cache under the home directory, say).
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          env:
{env_lines}
{volume_mount_lines}          # Without requests the scheduler packs pods blindly; without a
          # memory limit one leaking notebook can exhaust its node. No CPU
          # limit on purpose: it would throttle request bursts. Tune per model.
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
              ephemeral-storage: {ephemeral_request}
            limits:
              memory: 1Gi
              ephemeral-storage: {ephemeral_limit}
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /ready
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          # Notebooks often load a model or dataset at import time. Until this
          # probe succeeds the kubelet runs neither the liveness nor the
          # readiness probe, so a slow start (up to 30 x 10s = 5 minutes) is
          # not mistaken for a hang and restarted in a loop.
          startupProbe:
            httpGet:
              path: /health
              port: 8000
            periodSeconds: 10
            failureThreshold: 30
{volumes_lines}---
apiVersion: v1
kind: Service
metadata:
  name: {package_name_scalar}
spec:
  selector:
    app: {package_name_scalar}
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
{network_policy}{optional_resources}"""


def generate_kubernetes_manifest(
    output_path="generated/kubernetes.yaml",
    package_name="generated",
    env_vars=None,
    image=None,
    notebook_env_vars=None,
    announce=True,
):
    """Write a kubernetes.yaml for the compiled app at `output_path`,
    alongside the Dockerfile/.dockerignore/docker-compose.yml/.env.example/
    README.md generate_dockerfile/generate_dockerignore/
    generate_docker_compose/generate_env_example/generate_readme already
    write there on every compile -- see kubernetes_manifest_content's own
    docstring above for why this exists and what it contains, and for
    "image" (optional, defaulting identically) in particular.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(kubernetes_manifest_content(package_name, env_vars, image, notebook_env_vars))

    if announce:
        print(f"kubernetes.yaml generated at: {output_path}")
