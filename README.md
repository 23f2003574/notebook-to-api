# Notebook to API Compiler

A tool that compiles a Jupyter notebook into a FastAPI microservice, automatically handling:
- Extraction of code cells and functions
- Dependency detection and `requirements.txt` generation
- Generation of a Dockerfile for containerized deployment
- Creation of a runtime module to execute notebook code

## Usage
```bash
# Compile a notebook
.venv/bin/python backend/compiler.py --notebook notebooks/sample.ipynb --output generated/app.py
```
This will generate:
- `generated/app.py` – FastAPI app
- `generated/runtime/notebook_module.py` – Runtime module containing notebook code
- `generated/requirements.txt` – Dependencies (including detected imports)
- `generated/Dockerfile` – Dockerfile for container build

## Building the Docker Image
Make sure Docker is installed on your system.
```bash
docker build -t notebook-api generated/
```
Run the container:
```bash
docker run -p 8000:8000 notebook-api
```
Visit `http://localhost:8000/docs` to explore the generated API.

## Next Steps
- Implement one‑command deployment (`notebook-to-api deploy <notebook.ipynb>`)
- Add support for async functions, auth, and more advanced type handling.
