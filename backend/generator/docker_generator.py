def generate_dockerfile(output_path="generated/Dockerfile"):
    docker_content = """\
FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy generated output into /app/generated/ to preserve module paths
COPY . generated/

EXPOSE 8000

CMD ["uvicorn", "generated.app:app", "--host", "0.0.0.0", "--port", "8000"]
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(docker_content)

    print(f"Dockerfile generated at: {output_path}")
