from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Depends
import uuid
import os
import sys
from datetime import datetime
import time
from pydantic import BaseModel, Field
import generated.runtime.notebook_module as notebook_module

app = FastAPI(title="Notebook-to-API Generated Service", description="Automatically generated from notebook analysis.", version="1.0.0", contact={"name": "Notebook-to-API"}, license_info={"name": "MIT"}, servers=[{"url": "http://localhost:8000", "description": "Local development server"}])

app.openapi_schema = None

TASKS = {}
API_KEY = os.getenv("NOTEBOOK_API_KEY", "notebook-to-api-dev-key")
API_KEY_HEADER_NAME = 'X-API-Key'

def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail='Invalid API key'
        )

from fastapi.openapi.utils import get_openapi

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    openapi_schema.setdefault('components', {})
    openapi_schema['components'].setdefault('securitySchemes', {})

    openapi_schema['components']['securitySchemes']['ApiKeyAuth'] = {
        'type': 'apiKey',
        'in': 'header',
        'name': API_KEY_HEADER_NAME
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

START_TIME = time.time()
GENERATED_AT = datetime.utcnow().isoformat() + 'Z'
PYTHON_VERSION = sys.version.split()[0]

# Public infrastructure endpoints
@app.get('/')
def root():
    return {
        'service': 'Notebook-to-API Generated Service',
        'generator': 'notebook-to-api',
        'generator_version': '1.0.0',
        'generated_at': GENERATED_AT,
        'python_version': PYTHON_VERSION,
        'framework': 'FastAPI',
        'background_task_support': True,
        'background_endpoint_count': 0,
        'available_features': [
            'authentication',
            'background_tasks',
            'openapi_docs',
            'metrics',
            'task_monitoring',
            'health_checks'
        ],
        'documentation': {
            'swagger': '/docs',
            'openapi': '/openapi.json',
            'redoc': '/redoc'
        },
        'operations': {
            'health': '/health',
            'ready': '/ready',
            'info': '/info',
            'metrics': '/metrics',
            'uptime': '/uptime'
        },
        'task_management': {
            'list': '/tasks',
            'metrics': '/metrics',
            'cleanup': '/tasks/cleanup',
            'reset': '/tasks/reset'
        },
        'authentication': {
            'status': '/auth/status',
            'info': '/auth/info',
            'validate': '/auth/validate'
        },
        'endpoint_count': 3,
        'protected_endpoints': 3,
        'sample_endpoints': ['/add', '/multiply', '/predict']
    }

@app.get('/health')
def health_check():
    return {'status': 'healthy'}

@app.get('/ready')
def readiness_check():
    return {
        'status': 'ready',
        'tasks_registered': len(TASKS)
    }

@app.get('/auth/status')
def auth_status():
    return {
        'authentication': 'enabled',
        'api_key_configured': bool(API_KEY)
    }

@app.get('/auth/info')
def auth_info():
    return {
        'authentication': 'api_key',
        'header': API_KEY_HEADER_NAME,
        'environment_variable': 'NOTEBOOK_API_KEY',
        'rate_limiting': False,
        'key_rotation': True,
        'protected_endpoints': 3
    }

@app.get('/auth/validate')
def validate_auth(_: None = Depends(verify_api_key)):
    return {
        'authenticated': True
    }

@app.get('/info')
def service_info():
    return {
        "service": "Notebook-to-API Generated Service",
        "version": "1.0.0",
        "status": "running",
        "endpoints": ['/add', '/multiply', '/predict'],
        "endpoint_count": 3,
        "background_endpoint_count": 0,
        "authentication": {
            "enabled": True,
            "type": "api_key"
        }
    }

@app.get('/tasks')
def list_tasks():
    completed_tasks = sum(
        1
        for task in TASKS.values()
        if task.get('status') == 'completed'
    )
    failed_tasks = sum(
        1
        for task in TASKS.values()
        if task.get('status') == 'failed'
    )
    processing_tasks = sum(
        1
        for task in TASKS.values()
        if task.get('status') == 'processing'
    )
    return {
        'active_tasks': len(TASKS),
        'processing_tasks': processing_tasks,
        'completed_tasks': completed_tasks,
        'failed_tasks': failed_tasks,
        'tasks': TASKS
    }

@app.get('/tasks/{task_id}')
def get_task(task_id: str):
    task = TASKS.get(task_id)

    if not task:
        return {
            'error': 'Task not found'
        }

    return task

@app.delete('/tasks/completed')
def delete_completed_tasks():
    completed_task_ids = [
        task_id
        for task_id, task in TASKS.items()
        if task.get('status') == 'completed'
    ]
    for task_id in completed_task_ids:
        TASKS.pop(task_id, None)
    return {
        'deleted': len(completed_task_ids),
        'remaining_tasks': len(TASKS)
    }

@app.delete('/tasks/failed')
def delete_failed_tasks():
    failed_task_ids = [
        task_id
        for task_id, task in TASKS.items()
        if task.get('status') == 'failed'
    ]
    for task_id in failed_task_ids:
        TASKS.pop(task_id, None)
    return {
        'deleted': len(failed_task_ids),
        'remaining_tasks': len(TASKS)
    }

@app.post('/tasks/cleanup')
def cleanup_tasks():
    completed_deleted = 0
    failed_deleted = 0
    task_ids = list(TASKS.keys())
    for task_id in task_ids:
        status = TASKS[task_id].get('status')
        if status == 'completed':
            TASKS.pop(task_id, None)
            completed_deleted += 1
        elif status == 'failed':
            TASKS.pop(task_id, None)
            failed_deleted += 1
    return {
        'completed_deleted': completed_deleted,
        'failed_deleted': failed_deleted,
        'remaining_tasks': len(TASKS)
    }

@app.get('/metrics')
def metrics():
    processing = sum(
        1
        for task in TASKS.values()
        if task.get('status') == 'processing'
    )
    completed = sum(
        1
        for task in TASKS.values()
        if task.get('status') == 'completed'
    )
    failed = sum(
        1
        for task in TASKS.values()
        if task.get('status') == 'failed'
    )
    return {
        'total_tasks': len(TASKS),
        'processing': processing,
        'completed': completed,
        'failed': failed
    }

@app.get('/uptime')
def uptime():
    return {
        'uptime_seconds': int(time.time() - START_TIME)
    }

@app.post('/tasks/reset')
def reset_tasks():
    deleted_tasks = len(TASKS)
    TASKS.clear()
    return {
        'deleted_tasks': deleted_tasks
    }

@app.delete('/tasks/{task_id}')
def delete_task(task_id: str):
    if task_id not in TASKS:
        return {
            'error': 'Task not found'
        }
    deleted_task = TASKS.pop(task_id)
    return {
        'message': 'Task deleted',
        'task_id': task_id,
        'status': deleted_task.get('status')
    }

def _run_background_task(func, task_id, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
        TASKS[task_id]["status"] = "completed"
        TASKS[task_id]["result"] = result
    except Exception as e:
        TASKS[task_id]["status"] = "failed"
        TASKS[task_id]["error"] = str(e)

class AddRequest(BaseModel):
    a: int = Field(description="Parameter 'a' of type int")
    b: int = Field(description="Parameter 'b' of type int")

    model_config = {
        'json_schema_extra': {'example': {'a': 0, 'b': 0}}
    }

class MultiplyRequest(BaseModel):
    a: int = Field(description="Parameter 'a' of type int")
    b: int = Field(description="Parameter 'b' of type int")

    model_config = {
        'json_schema_extra': {'example': {'a': 0, 'b': 0}}
    }

class PredictRequest(BaseModel):
    age: int = Field(description="Parameter 'age' of type int")
    salary: float = Field(description="Parameter 'salary' of type float")
    city: str = Field(description="Parameter 'city' of type str")

    model_config = {
        'json_schema_extra': {'example': {'age': 0, 'salary': 0.0, 'city': ''}}
    }

@app.post("/add", summary="Add", description="Auto-generated endpoint for add. Operation ID: add. Parameters: a, b.", tags=["General"], operation_id="add", openapi_extra={"x-notebook-to-api-category": "General", "security": [{"ApiKeyAuth": []}]}, responses={200: {"description": "Returns int", "content": {"application/json": {"example": {'result': 0}}}}})
def add(req: AddRequest, _: None = Depends(verify_api_key)):
    result = notebook_module.add(req.a, req.b)
    return {"result": result}

@app.post("/multiply", summary="Multiply", description="Auto-generated endpoint for multiply. Operation ID: multiply. Parameters: a, b.", tags=["General"], operation_id="multiply", openapi_extra={"x-notebook-to-api-category": "General", "security": [{"ApiKeyAuth": []}]}, responses={200: {"description": "Returns int", "content": {"application/json": {"example": {'result': 0}}}}})
def multiply(req: MultiplyRequest, _: None = Depends(verify_api_key)):
    result = notebook_module.multiply(req.a, req.b)
    return {"result": result}

@app.post("/predict", summary="Predict", description="Auto-generated endpoint for predict. Operation ID: predict. Parameters: age, salary, city.", tags=["Inference"], operation_id="predict", openapi_extra={"x-notebook-to-api-category": "Inference", "security": [{"ApiKeyAuth": []}]}, responses={200: {"description": "Returns None", "content": {"application/json": {"example": {'result': None}}}}})
def predict(req: PredictRequest, _: None = Depends(verify_api_key)):
    result = notebook_module.predict(req.age, req.salary, req.city)
    return {"result": result}
