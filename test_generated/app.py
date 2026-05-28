from fastapi import FastAPI, BackgroundTasks
import uuid
from pydantic import BaseModel
import generated.runtime.notebook_module as notebook_module

app = FastAPI()

TASKS = {}

def _run_background_task(func, task_id, *args, **kwargs):
    try:
        result = func(*args, **kwargs)
        TASKS[task_id]["status"] = "completed"
        TASKS[task_id]["result"] = result
    except Exception as e:
        TASKS[task_id]["status"] = "failed"
        TASKS[task_id]["error"] = str(e)

class AddRequest(BaseModel):
    a: int
    b: int

class MultiplyRequest(BaseModel):
    a: int
    b: int

class PredictRequest(BaseModel):
    age: int
    salary: float
    city: str

@app.post('/add')
def add(req: AddRequest):
    result = notebook_module.add(req.a, req.b)
    return {"result": result}

@app.post('/multiply')
def multiply(req: MultiplyRequest):
    result = notebook_module.multiply(req.a, req.b)
    return {"result": result}

@app.post('/predict')
def predict(req: PredictRequest):
    result = notebook_module.predict(req.age, req.salary, req.city)
    return {"result": result}
