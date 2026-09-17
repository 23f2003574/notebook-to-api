import pytest

from backend.observability.health_checks import HealthCheckFramework
from backend.observability.service_discovery import ServiceDiscoveryMonitor, ServiceNode


def make_monitor(node_batches):
    calls = iter(node_batches)

    def scan_fn():
        return next(calls)

    return ServiceDiscoveryMonitor(scan_fn)


class TestTopologyDiscovery:
    def test_scan_returns_snapshot_of_current_nodes(self):
        monitor = make_monitor(
            [[ServiceNode(name="gateway", source="gateway", address="10.0.0.1")]]
        )

        snapshot = monitor.scan()

        assert [node.name for node in snapshot.nodes] == ["gateway"]
        assert snapshot.captured_at

    def test_node_rejects_unknown_source(self):
        with pytest.raises(ValueError):
            ServiceNode(name="gateway", source="unknown_source", address="10.0.0.1")


class TestServiceDetection:
    def test_detect_changes_reports_added_and_removed(self):
        monitor = make_monitor([[]])
        previous = monitor.scan()

        monitor_two = make_monitor(
            [
                [
                    ServiceNode(name="gateway", source="gateway", address="10.0.0.1"),
                    ServiceNode(name="worker-1", source="workers", address="10.0.0.2"),
                ]
            ]
        )
        current = monitor_two.scan()

        changes = monitor.detect_changes(previous, current)

        assert changes == {"added": ["gateway", "worker-1"], "removed": []}

    def test_detect_changes_reports_removed_nodes(self):
        monitor = make_monitor(
            [
                [ServiceNode(name="gateway", source="gateway", address="10.0.0.1")],
                [],
            ]
        )
        previous_snapshot = monitor.scan()
        current = monitor.scan()

        changes = monitor.detect_changes(previous_snapshot, current)

        assert changes == {"added": [], "removed": ["gateway"]}


class TestTopologyRefresh:
    def test_refresh_treats_first_scan_as_all_added(self):
        monitor = make_monitor(
            [[ServiceNode(name="gateway", source="gateway", address="10.0.0.1")]]
        )

        changes = monitor.refresh()

        assert changes == {"added": ["gateway"], "removed": []}

    def test_refresh_detects_changes_against_previous_snapshot(self):
        monitor = make_monitor(
            [
                [ServiceNode(name="gateway", source="gateway", address="10.0.0.1")],
                [ServiceNode(name="worker-1", source="workers", address="10.0.0.2")],
            ]
        )

        monitor.refresh()
        changes = monitor.refresh()

        assert changes == {"added": ["worker-1"], "removed": ["gateway"]}

    def test_topology_without_refresh_raises(self):
        monitor = make_monitor([[]])

        with pytest.raises(ValueError):
            monitor.topology()

    def test_topology_returns_latest_snapshot(self):
        monitor = make_monitor(
            [[ServiceNode(name="gateway", source="gateway", address="10.0.0.1")]]
        )

        monitor.refresh()
        snapshot = monitor.topology()

        assert [node.name for node in snapshot.nodes] == ["gateway"]


class TestDependencyMapping:
    def test_node_tracks_dependencies(self):
        node = ServiceNode(
            name="api",
            source="cluster",
            address="10.0.0.3",
            depends_on=["gateway", "db"],
        )

        assert node.depends_on == ["gateway", "db"]

    def test_register_from_topology_wires_dependency_checks(self):
        framework = HealthCheckFramework()
        gateway = ServiceNode(name="gateway", source="gateway", address="10.0.0.1")
        api = ServiceNode(
            name="api", source="cluster", address="10.0.0.3", depends_on=["gateway"]
        )

        framework.register_from_topology([gateway, api], check_fn=lambda node: True)

        assert framework.is_registered("gateway")
        assert framework.is_registered("api")
        report = framework.run("api")
        assert report.status == "healthy"

    def test_register_from_topology_propagates_unhealthy_dependency(self):
        framework = HealthCheckFramework()
        gateway = ServiceNode(name="gateway", source="gateway", address="10.0.0.1")
        api = ServiceNode(
            name="api", source="cluster", address="10.0.0.3", depends_on=["gateway"]
        )

        framework.register_from_topology(
            [gateway, api], check_fn=lambda node: node.name != "gateway"
        )

        report = framework.run("api")

        assert report.status == "unhealthy"
        assert "gateway" in report.error

    def test_register_from_topology_skips_already_registered(self):
        framework = HealthCheckFramework()
        gateway = ServiceNode(name="gateway", source="gateway", address="10.0.0.1")

        first = framework.register_from_topology([gateway], check_fn=lambda node: True)
        second = framework.register_from_topology([gateway], check_fn=lambda node: True)

        assert len(first) == 1
        assert len(second) == 0
