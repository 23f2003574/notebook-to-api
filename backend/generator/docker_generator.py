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

EXPOSE 8000

# The generated app already exposes GET /health for exactly this purpose
# (see api_generator.py); without a HEALTHCHECK, Docker/orchestrators
# (Compose, Swarm, a bare `docker run`) have no way to distinguish a
# hung/crashed process from a healthy one.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "{package_name}.app:app", "--host", "0.0.0.0", "--port", "8000"]
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
