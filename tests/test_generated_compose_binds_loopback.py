"""Regression: the generated docker-compose.yml must publish the app on
loopback by default (it ships with the public default API key and `*` CORS),
while NOTEBOOK_API_BIND_ADDRESS opts in to other interfaces."""
import re

from backend.generator.docker_generator import docker_compose_content


def _ports_line(content):
    return re.search(r'^\s+- "(.+:.+)"$', content, re.M).group(1)


def test_compose_publishes_on_loopback_by_default():
    mapping = _ports_line(docker_compose_content("generated", []))
    assert mapping.startswith("${NOTEBOOK_API_BIND_ADDRESS:-127.0.0.1}:")
    assert not mapping.startswith("${PORT")  # no bare, all-interfaces mapping


def test_compose_still_tracks_port_on_both_sides():
    mapping = _ports_line(docker_compose_content("generated", []))
    assert mapping.endswith("${PORT:-8000}:${PORT:-8000}")


def test_bind_address_override_is_documented_in_the_file():
    assert "NOTEBOOK_API_BIND_ADDRESS" in docker_compose_content("generated", [])
