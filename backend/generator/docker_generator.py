def generate_dockerfile(
    output_path="generated/Dockerfile",
    package_name="generated",
    python_version="3.11",
):
    """Write a Dockerfile for the compiled app at `output_path`.

    python_version selects the base image's Python ("<major>.<minor>",
    e.g. "3.12") and should be the interpreter that actually ran the
    compile -- see compiler.compiling_python_version(), the caller
    compile_notebook_to_api always passes. requirements.txt's versions are
    pinned by _pinned_requirement against whatever's installed in *that*
    interpreter's environment; a fixed base image Python unrelated to it
    (this previously always hardcoded "3.11" regardless of what compiled
    the notebook) can silently break `docker build`'s
    `pip install -r requirements.txt` the moment a pinned package's wheels
    don't cover that Python version, or fall back to a source build that
    behaves differently from what was actually resolved and tested
    locally.
    """
    docker_content = f"""\
FROM python:{python_version}-slim

# Python buffers stdout/stderr in blocks (not line-by-line) whenever it
# isn't attached to a real terminal -- which a container's stdout never
# is. Left unset, uvicorn's own request logs and any print() the
# notebook's own code does (it runs for real inside every request this
# app handles) can sit in that buffer for a long time, or be lost
# entirely if the container is killed before it fills -- exactly the
# output `docker logs` and any log-aggregation pipeline watching it are
# expected to see in real time.
#
# PYTHONDONTWRITEBYTECODE avoids writing a .pyc bytecode cache into the
# container's writable layer on every cold start: this project already
# treats __pycache__ as noise to actively exclude, not ship, everywhere
# else it could appear (see EXCLUDED_GENERATED_DIR_NAMES in
# backend/inspector.py, which keeps it out of `inspect`'s generated-files
# listing and the downloadable app bundle for the same reason) -- letting
# the deployed container itself accumulate one anyway, silently, was the
# one place that exclusion never reached. Also matters for a container
# run with a read-only root filesystem (a common hardening practice),
# where writing it at all would fail.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy generated output into /app/{package_name}/ to preserve module paths
COPY . {package_name}/

# Running as root inside the container is privilege the app never needs,
# and widens the blast radius of any RCE-class bug in this generated code
# or a transitive dependency.
RUN useradd --create-home --uid 1000 appuser \\
    && chown -R appuser:appuser /app
USER appuser

# 8000 is this image's default listening port for a plain `docker run`
# with no further configuration, but most real PaaS deploy targets
# (Cloud Run, Render, Heroku, ...) assign the container a port at
# deploy/start time via a $PORT environment variable and require the
# process to actually bind to it -- there's no fixed port they'll agree to
# forward to instead. EXPOSE is just image metadata (it can't reference an
# env var and doesn't affect what the process actually binds to), so it
# stays a literal 8000 documenting the default; the CMD and HEALTHCHECK
# below are what must actually track $PORT at container start.
EXPOSE 8000

# The generated app already exposes GET /health for exactly this purpose
# (see api_generator.py); without a HEALTHCHECK, Docker/orchestrators
# (Compose, Swarm, a bare `docker run`) have no way to distinguish a
# hung/crashed process from a healthy one. Reads $PORT the same way the
# CMD below does, so the healthcheck still hits the port uvicorn actually
# bound to instead of a stale hardcoded 8000.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT', '8000') + '/health', timeout=2)" || exit 1

# A plain exec-form `CMD ["uvicorn", ..., "--port", "8000"]` can't read an
# environment variable at all -- there's no shell involved to expand one --
# so the port would stay hardcoded to 8000 no matter what $PORT a deploy
# target set. Routing through `sh -c` lets `${{PORT:-8000}}` actually
# expand at container start, falling back to 8000 when $PORT isn't set
# (e.g. a local `docker run` with no other configuration).
CMD ["sh", "-c", "uvicorn {package_name}.app:app --host 0.0.0.0 --port ${{PORT:-8000}}"]
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(docker_content)

    print(f"Dockerfile generated at: {output_path}")


def generate_dockerignore(output_path="generated/.dockerignore"):
    """Without this, `COPY . {package_name}/` in the generated Dockerfile
    picks up .git, __pycache__, local venvs, notebooks, and other
    unrelated files from the build context into the image -- bloating it
    and, for .git in particular, potentially leaking history that was
    never meant to ship.
    """
    dockerignore_content = """\
.git/
.gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
venv/
env/
*.ipynb
.ipynb_checkpoints/
Dockerfile
.dockerignore
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(dockerignore_content)

    print(f".dockerignore generated at: {output_path}")
