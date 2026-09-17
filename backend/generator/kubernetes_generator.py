import re

from backend.exporters.openapi_exporter import _yaml_scalar

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


def kubernetes_manifest_content(package_name="generated", env_vars=None, image=None):
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
    image = image or f"{package_name.lower()}:latest"

    package_name_scalar = _yaml_scalar(_k8s_resource_name(package_name))
    image_scalar = _yaml_scalar(image)

    env_entries = [{"name": "PORT", "default": "8000"}] + list(env_vars)

    env_lines = "\n".join(
        f'            - name: {_yaml_scalar(entry["name"])}\n'
        f'              value: {_yaml_scalar(entry["default"])}'
        for entry in env_entries
    )

    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {package_name_scalar}
  labels:
    app: {package_name_scalar}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {package_name_scalar}
  template:
    metadata:
      labels:
        app: {package_name_scalar}
    spec:
      containers:
        - name: {package_name_scalar}
          image: {image_scalar}
          ports:
            - containerPort: 8000
          env:
{env_lines}
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
---
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
"""


def generate_kubernetes_manifest(
    output_path="generated/kubernetes.yaml",
    package_name="generated",
    env_vars=None,
    image=None,
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
        f.write(kubernetes_manifest_content(package_name, env_vars, image))

    print(f"kubernetes.yaml generated at: {output_path}")
