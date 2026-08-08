def generate_dockerfile(output_path="generated/Dockerfile", package_name="generated"):
    docker_content = f"""\
FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy generated output into /app/{package_name}/ to preserve module paths
COPY . {package_name}/

EXPOSE 8000

CMD ["uvicorn", "{package_name}.app:app", "--host", "0.0.0.0", "--port", "8000"]
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(docker_content)

    print(f"Dockerfile generated at: {output_path}")
