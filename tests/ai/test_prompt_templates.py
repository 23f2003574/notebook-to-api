import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.prompt_templates import (
    MissingVariableError,
    PromptTemplate,
    PromptTemplateManager,
    TemplateAlreadyExistsError,
    TemplateValidationError,
    TemplateVariable,
    UnknownTemplateError,
    get_prompt_template_manager,
    router as prompt_templates_router,
)


@pytest.fixture
def manager() -> PromptTemplateManager:
    return PromptTemplateManager()


@pytest.fixture
def client(manager: PromptTemplateManager) -> TestClient:
    app = FastAPI()
    app.include_router(prompt_templates_router)
    app.dependency_overrides[get_prompt_template_manager] = lambda: manager
    return TestClient(app)


def test_create_registers_template(manager: PromptTemplateManager):
    template = manager.create(
        "greeting", "Hello {name}, welcome to {product}!",
        [TemplateVariable(name="name"), TemplateVariable(name="product")],
    )

    assert isinstance(template, PromptTemplate)
    assert template.version == 1
    assert len(template.variables) == 2


def test_create_rejects_duplicate_name(manager: PromptTemplateManager):
    manager.create("greeting", "Hello {name}", [TemplateVariable(name="name")])

    with pytest.raises(TemplateAlreadyExistsError):
        manager.create("greeting", "Hi {name}", [TemplateVariable(name="name")])


def test_create_rejects_undeclared_variable(manager: PromptTemplateManager):
    with pytest.raises(TemplateValidationError):
        manager.create("greeting", "Hello {name}", [])


def test_create_rejects_unused_declared_variable(manager: PromptTemplateManager):
    with pytest.raises(TemplateValidationError):
        manager.create("greeting", "Hello there", [TemplateVariable(name="name")])


def test_render_substitutes_variables(manager: PromptTemplateManager):
    manager.create(
        "greeting", "Hello {name}, welcome to {product}!",
        [TemplateVariable(name="name"), TemplateVariable(name="product")],
    )

    rendered = manager.render("greeting", {"name": "Ada", "product": "Notebook API"})

    assert rendered == "Hello Ada, welcome to Notebook API!"


def test_render_uses_default_for_missing_optional_variable(manager: PromptTemplateManager):
    manager.create(
        "greeting", "Hello {name}",
        [TemplateVariable(name="name", required=False, default="friend")],
    )

    rendered = manager.render("greeting", {})

    assert rendered == "Hello friend"


def test_render_raises_for_missing_required_variable(manager: PromptTemplateManager):
    manager.create("greeting", "Hello {name}", [TemplateVariable(name="name")])

    with pytest.raises(MissingVariableError):
        manager.render("greeting", {})


def test_render_unknown_template_raises(manager: PromptTemplateManager):
    with pytest.raises(UnknownTemplateError):
        manager.render("does-not-exist", {})


def test_update_bumps_version_and_revalidates(manager: PromptTemplateManager):
    manager.create("greeting", "Hello {name}", [TemplateVariable(name="name")])

    updated = manager.update(
        "greeting", text="Hi {name}, {name}!", variables=[TemplateVariable(name="name")]
    )

    assert updated.version == 2
    assert updated.text == "Hi {name}, {name}!"


def test_update_rejects_invalid_result(manager: PromptTemplateManager):
    manager.create("greeting", "Hello {name}", [TemplateVariable(name="name")])

    with pytest.raises(TemplateValidationError):
        manager.update("greeting", text="Hello {other}")


def test_update_unknown_template_raises(manager: PromptTemplateManager):
    with pytest.raises(UnknownTemplateError):
        manager.update("does-not-exist", text="Hi")


def test_delete_removes_template(manager: PromptTemplateManager):
    manager.create("greeting", "Hello {name}", [TemplateVariable(name="name")])

    manager.delete("greeting")

    with pytest.raises(UnknownTemplateError):
        manager.get("greeting")


def test_delete_unknown_template_raises(manager: PromptTemplateManager):
    with pytest.raises(UnknownTemplateError):
        manager.delete("does-not-exist")


def test_list_templates_returns_all(manager: PromptTemplateManager):
    manager.create("greeting", "Hello {name}", [TemplateVariable(name="name")])
    manager.create("farewell", "Bye {name}", [TemplateVariable(name="name")])

    listed = manager.list_templates()

    assert [template.name for template in listed] == ["farewell", "greeting"]


def test_api_create_and_get_template(client: TestClient):
    response = client.post(
        "/ai/prompts",
        json={"name": "greeting", "text": "Hello {name}", "variables": [{"name": "name"}]},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "greeting"

    fetched = client.get("/ai/prompts/greeting")
    assert fetched.status_code == 200
    assert fetched.json()["text"] == "Hello {name}"


def test_api_create_invalid_template_returns_422(client: TestClient):
    response = client.post("/ai/prompts", json={"name": "greeting", "text": "Hello {name}"})

    assert response.status_code == 422


def test_api_create_duplicate_returns_409(client: TestClient):
    client.post("/ai/prompts", json={"name": "greeting", "text": "Hi", "variables": []})
    response = client.post("/ai/prompts", json={"name": "greeting", "text": "Hi", "variables": []})

    assert response.status_code == 409


def test_api_list_templates(client: TestClient):
    client.post("/ai/prompts", json={"name": "greeting", "text": "Hi", "variables": []})
    client.post("/ai/prompts", json={"name": "farewell", "text": "Bye", "variables": []})

    response = client.get("/ai/prompts")

    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_get_unknown_template_returns_404(client: TestClient):
    response = client.get("/ai/prompts/does-not-exist")

    assert response.status_code == 404


def test_api_delete_template(client: TestClient):
    client.post("/ai/prompts", json={"name": "greeting", "text": "Hi", "variables": []})

    response = client.delete("/ai/prompts/greeting")
    assert response.status_code == 204

    assert client.get("/ai/prompts/greeting").status_code == 404


def test_api_delete_unknown_template_returns_404(client: TestClient):
    response = client.delete("/ai/prompts/does-not-exist")

    assert response.status_code == 404
