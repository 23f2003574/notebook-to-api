import pytest

from backend.generator.api_generator import GENERATED_APP_ENV_VARS
from backend.generator.docker_generator import docker_compose_content, env_example_content
from backend.generator.kubernetes_generator import kubernetes_manifest_content

LIMIT_VARS = [
    "NOTEBOOK_API_REQUEST_TIMEOUT_SECONDS",
    "NOTEBOOK_API_ISOLATE_EXECUTION",
    "NOTEBOOK_API_ISOLATE_MEMORY_LIMIT_MB",
    "NOTEBOOK_API_ISOLATE_CPU_LIMIT_SECONDS",
    "NOTEBOOK_API_ISOLATE_MAX_PROCESSES",
]


def _default(name):
    return next(e["default"] for e in GENERATED_APP_ENV_VARS if e["name"] == name)


@pytest.mark.parametrize("name", LIMIT_VARS)
def test_limits_default_to_disabled_in_the_app_declaration(name):
    assert _default(name) == "0"


@pytest.mark.parametrize("name", LIMIT_VARS)
def test_compose_passes_limit_through_with_unset_default(name):
    content = docker_compose_content(env_vars=GENERATED_APP_ENV_VARS)
    assert f"      - {name}=${{{name}:-0}}" in content


@pytest.mark.parametrize("name", LIMIT_VARS)
def test_kubernetes_manifest_declares_limit_with_unset_default(name):
    content = kubernetes_manifest_content(env_vars=GENERATED_APP_ENV_VARS)
    assert f"            - name: {name}\n              value: \"0\"" in content or \
        f"            - name: {name}\n              value: '0'" in content or \
        f"            - name: {name}\n              value: 0" in content


@pytest.mark.parametrize("name", LIMIT_VARS)
def test_env_example_lists_limit(name):
    assert name in env_example_content(env_vars=GENERATED_APP_ENV_VARS)


def test_no_limit_is_hardcoded_to_a_nonzero_value():
    compose = docker_compose_content(env_vars=GENERATED_APP_ENV_VARS)
    for name in LIMIT_VARS:
        assert f"{name}=${{{name}:-0}}" in compose
