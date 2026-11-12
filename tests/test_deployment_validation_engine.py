"""DeploymentValidationEngine reports what the real validators found."""
from backend.generator.deployment_validation import DeploymentValidationEngine
from backend.generator.docker_generator import docker_compose_content
from backend.generator.kubernetes_generator import kubernetes_manifest_content

ENV = [{"name": "NOTEBOOK_API_PORT", "default": "8000", "description": "x"}]
COMPOSE = docker_compose_content("app", ENV)
K8S = kubernetes_manifest_content("app", ENV)
DOCKERFILE = "FROM python:3.13-slim\n"


def _run(artifacts):
    return DeploymentValidationEngine().generate(artifacts)


def test_valid_artifacts_pass_and_each_is_counted():
    result = _run({"Dockerfile": DOCKERFILE, "docker-compose.yml": COMPOSE, "kubernetes.yaml": K8S})
    assert result.validation_passed is True and result.checks_performed == 3
    assert result.errors == [] and result.warnings == []


def test_invalid_manifest_fails_with_actionable_error_and_no_secret_values():
    bad = "services:\n  app:\n    build: .\n    environment:\n      - NOTEBOOK_API_KEY=hunter2\n      - NOTEBOOK_API_KEY=other\n"
    result = _run({"docker-compose.yml": bad, "kubernetes.yaml": K8S})
    assert result.validation_passed is False and result.checks_performed == 2
    assert any("duplicate environment variables" in e for e in result.errors)
    assert "hunter2" not in " ".join(result.errors)


def test_missing_or_malformed_input_fails_truthfully():
    for supplied in (None, {}, "text", ["x"]):
        result = _run(supplied)
        assert result.validation_passed is False and result.checks_performed == 0 and result.errors
    assert _run({"docker-compose.yml": ""}).validation_passed is False
    assert _run({"docker-compose.yml": "a: [unclosed"}).validation_passed is False
    assert _run({"Dockerfile": "RUN true"}).validation_passed is False


def test_checks_performed_counts_only_validators_that_ran():
    result = _run({"Dockerfile": DOCKERFILE, "notes.txt": "hello"})
    assert result.checks_performed == 1 and result.validation_passed is True
    assert result.warnings == ["notes.txt: no validator for this artifact; skipped"]
    only_unknown = _run({"notes.txt": "hello"})
    assert only_unknown.validation_passed is False and only_unknown.checks_performed == 0


def test_existing_call_shapes_still_work():
    from backend.generator.pipeline_schema_generator import PipelineSchemaGenerator  # noqa: F401
    result = DeploymentValidationEngine().generate()
    assert set(vars(result)) == {"validation_passed", "checks_performed", "warnings", "errors"}
