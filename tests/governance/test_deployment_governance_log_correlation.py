import json
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import Mock
from uuid import UUID

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceIntegrityLogger,
)
from backend.observability.deployment_governance_log_correlation import (
    GovernanceCorrelationContext,
    GovernanceCorrelationService,
)
from backend.observability.deployment_governance_delivery_worker import (
    GovernanceIntegrityDeliveryWorker,
)
from backend.observability.deployment_governance_delivery_scheduler import (
    GovernanceIntegrityScheduledDispatch,
    GovernanceIntegrityDispatchState,
)
from backend.observability.deployment_governance_delivery_engine import (
    GovernanceIntegrityDeliveryResult,
    GovernanceIntegrityDeliveryStatus,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_trace,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestGovernanceCorrelationContext:

    def test_rejects_self_referential_parent(self):
        correlation_id = UUID(
            "11111111-1111-1111-1111-111111111111"
        )

        with pytest.raises(ValueError):
            GovernanceCorrelationContext(
                correlation_id=correlation_id,
                parent_correlation_id=correlation_id,
            )

    def test_to_dict_root(self):
        correlation_id = UUID(
            "11111111-1111-1111-1111-111111111111"
        )

        context = GovernanceCorrelationContext(
            correlation_id=correlation_id,
            parent_correlation_id=None,
        )

        assert context.to_dict() == {
            "correlation_id": str(correlation_id),
            "parent_correlation_id": None,
        }

    def test_to_dict_child(self):
        correlation_id = UUID(
            "11111111-1111-1111-1111-111111111111"
        )
        parent_id = UUID("22222222-2222-2222-2222-222222222222")

        context = GovernanceCorrelationContext(
            correlation_id=correlation_id,
            parent_correlation_id=parent_id,
        )

        assert context.to_dict() == {
            "correlation_id": str(correlation_id),
            "parent_correlation_id": str(parent_id),
        }


class TestGovernanceCorrelationServiceRootCreation:

    def test_create_returns_root_with_no_parent(self):
        service = GovernanceCorrelationService()

        service.clear()

        context = service.create()

        assert context.parent_correlation_id is None
        assert context.correlation_id.version == 4

    def test_create_makes_context_current(self):
        service = GovernanceCorrelationService()

        service.clear()

        context = service.create()

        assert service.current() == context

    def test_successive_creates_produce_different_ids(self):
        service = GovernanceCorrelationService()

        service.clear()

        first = service.create()
        second = service.create()

        assert first.correlation_id != second.correlation_id


class TestGovernanceCorrelationServiceChild:

    def test_child_of_root_has_root_as_parent(self):
        service = GovernanceCorrelationService()

        service.clear()

        root = service.create()
        child = service.child()

        assert child.parent_correlation_id == root.correlation_id
        assert child.correlation_id != root.correlation_id

    def test_child_makes_itself_current(self):
        service = GovernanceCorrelationService()

        service.clear()

        service.create()
        child = service.child()

        assert service.current() == child

    def test_child_without_active_correlation_becomes_a_root(self):
        service = GovernanceCorrelationService()

        service.clear()

        child = service.child()

        assert child.parent_correlation_id is None

    def test_grandchild_chains_correctly(self):
        service = GovernanceCorrelationService()

        service.clear()

        root = service.create()
        child = service.child()
        grandchild = service.child()

        assert child.parent_correlation_id == root.correlation_id
        assert (
            grandchild.parent_correlation_id == child.correlation_id
        )


class TestGovernanceCorrelationServiceInheritance:

    def test_current_is_inherited_without_explicit_child_call(self):
        service = GovernanceCorrelationService()

        service.clear()

        root = service.create()

        # A nested operation that never calls child() sees the same
        # correlation as its caller.
        assert service.current() == root
        assert service.current() == root

    def test_attach_reuses_an_existing_context(self):
        service = GovernanceCorrelationService()

        service.clear()

        root = service.create()

        service.clear()

        assert service.current() is None

        service.attach(root)

        assert service.current() == root

    def test_clear_removes_active_correlation(self):
        service = GovernanceCorrelationService()

        service.create()

        service.clear()

        assert service.current() is None


class TestGovernanceCorrelationLoggerIntegration:

    def test_log_enrichment_includes_both_ids(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            correlation_service=correlation_service,
        )

        root = correlation_service.create()
        child = correlation_service.child()

        entry = logger.info("metrics", "record_success")

        assert entry.fields["correlation_id"] == str(
            child.correlation_id
        )
        assert entry.fields["parent_correlation_id"] == str(
            root.correlation_id
        )

    def test_root_correlation_has_none_parent_in_fields(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            correlation_service=correlation_service,
        )

        root = correlation_service.create()

        entry = logger.info("metrics", "record_success")

        assert entry.fields["correlation_id"] == str(
            root.correlation_id
        )
        assert entry.fields["parent_correlation_id"] is None
        assert "parent_correlation_id" in entry.fields

    def test_no_enrichment_when_no_correlation_active(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            correlation_service=correlation_service,
        )

        entry = logger.info("metrics", "record_success")

        assert "correlation_id" not in entry.fields
        assert "parent_correlation_id" not in entry.fields

    def test_no_enrichment_when_no_correlation_service_attached(
        self,
    ):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.info("metrics", "record_success")

        assert "correlation_id" not in entry.fields

    def test_explicit_correlation_id_field_overrides(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            correlation_service=correlation_service,
        )

        correlation_service.create()

        entry = logger.info(
            "metrics", "record_success", correlation_id="explicit"
        )

        assert entry.fields["correlation_id"] == "explicit"

    def test_set_correlation_service_attaches_after_construction(
        self,
    ):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        logger.set_correlation_service(correlation_service)

        root = correlation_service.create()

        entry = logger.info("metrics", "record_success")

        assert entry.fields["correlation_id"] == str(
            root.correlation_id
        )

    def test_correlation_and_context_merge_together(self):
        from backend.observability.deployment_governance_log_context import (
            GovernanceLogContext,
            GovernanceLogContextService,
        )

        context_service = GovernanceLogContextService()
        context_service.clear()

        correlation_service = GovernanceCorrelationService()
        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            context_service=context_service,
            correlation_service=correlation_service,
        )

        context_service.push(
            GovernanceLogContext(
                request_id=None,
                dispatch_id="dispatch-1",
                provider=None,
                component="delivery_worker",
            )
        )

        root = correlation_service.create()

        entry = logger.info("metrics", "record_success")

        context_service.pop()

        assert entry.fields["dispatch_id"] == "dispatch-1"
        assert entry.fields["correlation_id"] == str(
            root.correlation_id
        )


class TestGovernanceCorrelationWorkerIntegration:

    def _dispatch(self, attempt=0):
        return GovernanceIntegrityScheduledDispatch(
            dispatch_id=UUID(
                "11111111-1111-1111-1111-111111111111"
            ),
            scheduled_at=BASE_TIME,
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=attempt,
        )

    def _success_result(self, dispatch_id):
        return GovernanceIntegrityDeliveryResult(
            dispatch_id=dispatch_id,
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.SUCCESS,
            delivered_at=BASE_TIME,
            error=None,
        )

    def _failed_result(self, dispatch_id):
        return GovernanceIntegrityDeliveryResult(
            dispatch_id=dispatch_id,
            channel_name="email",
            status=GovernanceIntegrityDeliveryStatus.FAILED,
            delivered_at=BASE_TIME,
            error="boom",
        )

    def test_root_correlation_created_for_first_attempt(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        scheduler = Mock()

        delivery_engine = Mock()

        seen = []

        def _capture(dispatch_id):
            seen.append(correlation_service.current())

            return self._success_result(dispatch_id)

        delivery_engine.deliver.side_effect = _capture

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler,
            delivery_engine,
            Mock(),
            correlation_service=correlation_service,
        )

        worker.process_dispatch(self._dispatch(attempt=0))

        assert seen[0] is not None
        # The provider invocation gets a CHILD correlation, nested
        # under the dispatch's root.
        assert seen[0].parent_correlation_id is not None
        assert correlation_service.current() is None

    def test_retry_reuses_same_root_correlation(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        scheduler = Mock()

        retry_orchestrator = Mock()

        seen_parent_ids = []

        delivery_engine = Mock()

        def _capture_and_fail(dispatch_id):
            current = correlation_service.current()

            seen_parent_ids.append(current.parent_correlation_id)

            return self._failed_result(dispatch_id)

        delivery_engine.deliver.side_effect = _capture_and_fail

        decision = Mock()
        decision.should_retry = True
        decision.retry_attempt = 1
        decision.delay_seconds = 30

        retry_orchestrator.evaluate_delivery_result.return_value = (
            decision
        )

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler,
            delivery_engine,
            retry_orchestrator,
            correlation_service=correlation_service,
        )

        # First attempt: retryable failure.
        outcome_one = worker.process_dispatch(
            self._dispatch(attempt=0)
        )

        assert outcome_one == "retried"

        # Second attempt (the retry): same dispatch id.
        outcome_two = worker.process_dispatch(
            self._dispatch(attempt=1)
        )

        assert outcome_two == "retried"

        # Both attempts' provider-invocation child correlations must
        # share the same root (parent_correlation_id).
        assert seen_parent_ids[0] is not None
        assert seen_parent_ids[0] == seen_parent_ids[1]

    def test_root_correlation_forgotten_after_terminal_outcome(self):
        correlation_service = GovernanceCorrelationService()

        correlation_service.clear()

        scheduler = Mock()

        delivery_engine = Mock()
        delivery_engine.deliver.side_effect = (
            lambda dispatch_id: self._success_result(dispatch_id)
        )

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler,
            delivery_engine,
            Mock(),
            correlation_service=correlation_service,
        )

        worker.process_dispatch(self._dispatch(attempt=0))

        assert (
            worker._dispatch_root_correlations == {}
        )

    def test_worker_without_correlation_service_still_works(self):
        scheduler = Mock()

        delivery_engine = Mock()
        delivery_engine.deliver.return_value = self._success_result(
            "11111111-1111-1111-1111-111111111111"
        )

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler, delivery_engine, Mock()
        )

        outcome = worker.process_dispatch(self._dispatch())

        assert outcome == "succeeded"


class TestGovernanceCorrelationCli:

    def _stub_runtime(self, search_service):
        class _StubRuntime:
            def build_integrity_log_search_service(self):
                return search_service

        return _StubRuntime()

    def test_trace_runner_matches_by_own_correlation_id(
        self, monkeypatch
    ):
        from backend.observability.deployment_governance_log_repository import (
            InMemoryGovernanceLogRepository,
        )
        from backend.observability.deployment_governance_log_search import (
            GovernanceLogSearchService,
        )

        repository = InMemoryGovernanceLogRepository()

        correlation_service = GovernanceCorrelationService()
        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            correlation_service=correlation_service,
        )

        root = correlation_service.create()

        logger.info("metrics", "record_success")

        search_service = GovernanceLogSearchService(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(search_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_trace(
            correlation_id=str(root.correlation_id),
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 1
        assert payload[0]["fields"]["correlation_id"] == str(
            root.correlation_id
        )

    def test_trace_runner_matches_children_by_parent_id(
        self, monkeypatch
    ):
        from backend.observability.deployment_governance_log_repository import (
            InMemoryGovernanceLogRepository,
        )
        from backend.observability.deployment_governance_log_search import (
            GovernanceLogSearchService,
        )

        repository = InMemoryGovernanceLogRepository()

        correlation_service = GovernanceCorrelationService()
        correlation_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            correlation_service=correlation_service,
        )

        root = correlation_service.create()
        correlation_service.child()

        logger.info("metrics", "attempt_one_event")

        search_service = GovernanceLogSearchService(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(search_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_trace(
            correlation_id=str(root.correlation_id),
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert len(payload) == 1
        assert payload[0]["event"] == "attempt_one_event"

    def test_trace_runner_returns_chronological_order(
        self, monkeypatch
    ):
        from backend.observability.deployment_governance_log_repository import (
            InMemoryGovernanceLogRepository,
        )
        from backend.observability.deployment_governance_log_search import (
            GovernanceLogSearchService,
        )

        repository = InMemoryGovernanceLogRepository()

        correlation_service = GovernanceCorrelationService()
        correlation_service.clear()

        timestamps = iter(
            [
                BASE_TIME,
                BASE_TIME.replace(minute=1),
                BASE_TIME.replace(minute=2),
            ]
        )

        logger = GovernanceIntegrityLogger(
            clock=lambda: next(timestamps),
            repository=repository,
            correlation_service=correlation_service,
        )

        root = correlation_service.create()

        logger.info("metrics", "first")
        logger.info("metrics", "second")
        logger.info("metrics", "third")

        search_service = GovernanceLogSearchService(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(search_service),
        )

        stdout = StringIO()

        run_deployment_governance_logging_trace(
            correlation_id=str(root.correlation_id),
            json_output=True,
            stdout=stdout,
            stderr=StringIO(),
        )

        payload = json.loads(stdout.getvalue())

        assert [item["event"] for item in payload] == [
            "first",
            "second",
            "third",
        ]

    def test_trace_runner_handles_no_matches(self, monkeypatch):
        from backend.observability.deployment_governance_log_repository import (
            InMemoryGovernanceLogRepository,
        )
        from backend.observability.deployment_governance_log_search import (
            GovernanceLogSearchService,
        )

        repository = InMemoryGovernanceLogRepository()

        search_service = GovernanceLogSearchService(repository)

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(search_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_trace(
            correlation_id="11111111-1111-1111-1111-111111111111",
            stdout=stdout,
            stderr=StringIO(),
        )

        assert exit_code == 0
        assert "No governance log entries" in stdout.getvalue()
