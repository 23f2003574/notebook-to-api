import json
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import Mock
from uuid import UUID

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceIntegrityLogger,
)
from backend.observability.deployment_governance_log_context import (
    GovernanceLogContext,
    GovernanceLogContextService,
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
    run_deployment_governance_logging_context,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _context(**overrides) -> GovernanceLogContext:
    fields = {
        "request_id": None,
        "dispatch_id": None,
        "provider": None,
        "component": "test",
    }

    fields.update(overrides)

    return GovernanceLogContext(**fields)


class TestGovernanceLogContext:

    def test_rejects_empty_component(self):
        with pytest.raises(ValueError):
            GovernanceLogContext(
                request_id=None,
                dispatch_id=None,
                provider=None,
                component="",
            )

    def test_to_dict(self):
        context = _context(
            request_id="req-1",
            dispatch_id="dispatch-1",
            provider="webhook",
            component="delivery_engine",
        )

        assert context.to_dict() == {
            "request_id": "req-1",
            "dispatch_id": "dispatch-1",
            "provider": "webhook",
            "component": "delivery_engine",
        }


class TestGovernanceLogContextServicePushPop:

    def test_current_is_none_when_empty(self):
        service = GovernanceLogContextService()

        service.clear()

        assert service.current() is None

    def test_push_then_current(self):
        service = GovernanceLogContextService()

        service.clear()

        context = _context(component="a")

        service.push(context)

        assert service.current() == context

        service.pop()

    def test_pop_restores_previous_state(self):
        service = GovernanceLogContextService()

        service.clear()

        context = _context(component="a")

        service.push(context)

        popped = service.pop()

        assert popped == context
        assert service.current() is None

    def test_pop_on_empty_stack_is_a_no_op(self):
        service = GovernanceLogContextService()

        service.clear()

        assert service.pop() is None
        assert service.current() is None

    def test_clear_discards_every_scope(self):
        service = GovernanceLogContextService()

        service.clear()

        service.push(_context(component="a"))
        service.push(_context(component="b"))

        service.clear()

        assert service.current() is None


class TestGovernanceLogContextServiceNesting:

    def test_current_returns_innermost_scope(self):
        service = GovernanceLogContextService()

        service.clear()

        outer = _context(component="outer")
        inner = _context(component="inner")

        service.push(outer)
        service.push(inner)

        assert service.current() == inner

        service.pop()

        assert service.current() == outer

        service.pop()

    def test_deeply_nested_scopes(self):
        service = GovernanceLogContextService()

        service.clear()

        contexts = [_context(component=f"level_{i}") for i in range(5)]

        for context in contexts:
            service.push(context)

        for context in reversed(contexts):
            assert service.current() == context
            service.pop()

        assert service.current() is None


class TestGovernanceLogContextServiceCleanup:

    def test_try_finally_cleanup_restores_state_after_exception(
        self,
    ):
        service = GovernanceLogContextService()

        service.clear()

        outer = _context(component="outer")

        service.push(outer)

        with pytest.raises(RuntimeError):
            service.push(_context(component="inner"))

            try:
                raise RuntimeError("boom")

            finally:
                service.pop()

        assert service.current() == outer

        service.pop()

    def test_clear_is_the_ultimate_cleanup(self):
        service = GovernanceLogContextService()

        service.push(_context(component="a"))
        service.push(_context(component="b"))
        service.push(_context(component="c"))

        service.clear()

        assert service.current() is None


class TestGovernanceLogContextLoggerIntegration:

    def test_automatic_field_injection(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            context_service=context_service,
        )

        context_service.push(
            _context(
                request_id="req-1",
                dispatch_id="dispatch-1",
                provider="webhook",
                component="delivery_engine",
            )
        )

        entry = logger.info("metrics", "record_success")

        context_service.pop()

        assert entry.fields["request_id"] == "req-1"
        assert entry.fields["dispatch_id"] == "dispatch-1"
        assert entry.fields["provider"] == "webhook"
        assert entry.fields["component"] == "delivery_engine"

    def test_no_injection_when_no_context_active(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            context_service=context_service,
        )

        entry = logger.info("metrics", "record_success")

        assert entry.fields == {}

    def test_no_injection_when_no_context_service_attached(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.info("metrics", "record_success")

        assert entry.fields == {}

    def test_explicit_field_overrides_context_value(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            context_service=context_service,
        )

        context_service.push(
            _context(dispatch_id="context-dispatch", component="x")
        )

        entry = logger.info(
            "metrics", "record_success", dispatch_id="explicit-dispatch"
        )

        context_service.pop()

        assert entry.fields["dispatch_id"] == "explicit-dispatch"

    def test_none_context_fields_are_not_injected(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            context_service=context_service,
        )

        context_service.push(
            _context(
                request_id=None,
                dispatch_id="dispatch-1",
                provider=None,
                component="delivery_worker",
            )
        )

        entry = logger.info("metrics", "record_success")

        context_service.pop()

        assert "request_id" not in entry.fields
        assert "provider" not in entry.fields
        assert entry.fields["dispatch_id"] == "dispatch-1"

    def test_nested_scope_merges_innermost_context(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            context_service=context_service,
        )

        context_service.push(
            _context(component="delivery_runtime", request_id="req-1")
        )
        context_service.push(
            _context(component="delivery_engine", dispatch_id="d1")
        )

        entry = logger.info("metrics", "record_success")

        context_service.pop()
        context_service.pop()

        assert entry.fields["component"] == "delivery_engine"
        assert entry.fields["dispatch_id"] == "d1"
        assert "request_id" not in entry.fields

    def test_set_context_service_attaches_after_construction(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        logger.set_context_service(context_service)

        context_service.push(_context(dispatch_id="d1", component="x"))

        entry = logger.info("metrics", "record_success")

        context_service.pop()

        assert entry.fields["dispatch_id"] == "d1"


class TestGovernanceLogContextRuntimeIntegrationWorker:

    def _dispatch(self):
        return GovernanceIntegrityScheduledDispatch(
            dispatch_id=UUID(
                "11111111-1111-1111-1111-111111111111"
            ),
            scheduled_at=BASE_TIME,
            state=GovernanceIntegrityDispatchState.PENDING,
            attempt=0,
        )

    def test_worker_pushes_and_pops_context_around_dispatch(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        scheduler = Mock()

        delivery_engine = Mock()

        seen_context_during_delivery = []

        def _capture_context(dispatch_id):
            seen_context_during_delivery.append(
                context_service.current()
            )

            return GovernanceIntegrityDeliveryResult(
                dispatch_id=dispatch_id,
                channel_name="email",
                status=GovernanceIntegrityDeliveryStatus.SUCCESS,
                delivered_at=BASE_TIME,
                error=None,
            )

        delivery_engine.deliver.side_effect = _capture_context

        retry_orchestrator = Mock()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler,
            delivery_engine,
            retry_orchestrator,
            context_service=context_service,
        )

        outcome = worker.process_dispatch(self._dispatch())

        assert outcome == "succeeded"
        assert seen_context_during_delivery[0] is not None
        assert (
            seen_context_during_delivery[0].component
            == "delivery_worker"
        )
        assert (
            seen_context_during_delivery[0].dispatch_id
            == "11111111-1111-1111-1111-111111111111"
        )
        assert context_service.current() is None

    def test_worker_pops_context_even_on_exception(self):
        context_service = GovernanceLogContextService()

        context_service.clear()

        scheduler = Mock()

        delivery_engine = Mock()
        delivery_engine.deliver.side_effect = RuntimeError("boom")

        retry_orchestrator = Mock()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler,
            delivery_engine,
            retry_orchestrator,
            context_service=context_service,
        )

        outcome = worker.process_dispatch(self._dispatch())

        assert outcome == "failed"
        assert context_service.current() is None

    def test_worker_without_context_service_still_works(self):
        scheduler = Mock()

        delivery_engine = Mock()
        delivery_engine.deliver.return_value = (
            GovernanceIntegrityDeliveryResult(
                dispatch_id="dispatch-1",
                channel_name="email",
                status=GovernanceIntegrityDeliveryStatus.SUCCESS,
                delivered_at=BASE_TIME,
                error=None,
            )
        )

        retry_orchestrator = Mock()

        worker = GovernanceIntegrityDeliveryWorker(
            scheduler, delivery_engine, retry_orchestrator
        )

        assert worker.process_dispatch(self._dispatch()) == "succeeded"


class TestGovernanceLogContextCli:

    def _stub_runtime(self, context_service):
        class _StubRuntime:
            def build_integrity_log_context_service(self):
                return context_service

        return _StubRuntime()

    def test_context_runner_shows_nested_scopes(self, monkeypatch):
        context_service = GovernanceLogContextService()

        context_service.clear()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(context_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_context(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["before"] is None
        assert payload["during_outer_scope"]["component"] == (
            "delivery_runtime"
        )
        assert payload["during_nested_scope"]["component"] == (
            "delivery_engine"
        )
        assert payload["after_inner_pop"]["component"] == (
            "delivery_runtime"
        )
        assert payload["after_outer_pop"] is None

    def test_context_runner_leaves_no_residual_state(
        self, monkeypatch
    ):
        context_service = GovernanceLogContextService()

        context_service.clear()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(context_service),
        )

        run_deployment_governance_logging_context(
            stdout=StringIO(), stderr=StringIO()
        )

        assert context_service.current() is None
