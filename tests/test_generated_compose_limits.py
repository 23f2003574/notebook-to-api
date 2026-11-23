"""Generated docker-compose.yml bounds memory, CPU and process count, with overrides."""
import yaml

from backend.generator.docker_generator import (
    COMPOSE_ONLY_ENV_VARS, docker_compose_content, env_example_content, readme_content,
)

LIMITS = ("NOTEBOOK_API_MEMORY_LIMIT", "NOTEBOOK_API_CPU_LIMIT", "NOTEBOOK_API_PIDS_LIMIT")


def _service():
    return next(iter(yaml.safe_load(docker_compose_content("nb"))["services"].values()))


def test_compose_is_valid_yaml_with_bounded_defaults_and_overrides():
    service = _service()
    assert service["mem_limit"] == "${NOTEBOOK_API_MEMORY_LIMIT:-1g}"
    assert service["cpus"] == "${NOTEBOOK_API_CPU_LIMIT:-2}"
    assert service["pids_limit"] == "${NOTEBOOK_API_PIDS_LIMIT:-256}"
    assert service["healthcheck"]["retries"] == 3 and service["cap_drop"] == ["ALL"]


def test_limits_are_compose_only_documented_and_in_the_readme():
    assert set(LIMITS) <= COMPOSE_ONLY_ENV_VARS
    env_text, readme = env_example_content(), readme_content("nb")
    for name in LIMITS:
        assert f"# {name}=" in env_text
        assert name in readme


