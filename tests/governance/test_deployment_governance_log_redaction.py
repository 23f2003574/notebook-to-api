import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest

from backend.observability.deployment_governance_logging import (
    GovernanceIntegrityLogger,
    GovernanceLogEntry,
)
from backend.observability.deployment_governance_log_repository import (
    InMemoryGovernanceLogRepository,
)
from backend.observability.deployment_governance_log_search import (
    GovernanceLogSearchService,
)
from backend.observability.deployment_governance_log_export import (
    GovernanceLogExportService,
)
from backend.observability.deployment_governance_log_redaction import (
    DEFAULT_REDACTED_FIELDS,
    DEFAULT_REDACTION_REPLACEMENT,
    GovernanceLogRedactionRule,
    GovernanceLogRedactionService,
)
from backend.observability.deployment_governance_logging_cli import (
    run_deployment_governance_logging_redaction_rules,
    run_deployment_governance_logging_redaction_test,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _entry(**fields) -> GovernanceLogEntry:
    return GovernanceLogEntry(
        timestamp=BASE_TIME,
        level="INFO",
        component="metrics",
        event="record_success",
        fields=fields,
    )


class TestGovernanceLogRedactionRule:

    def test_rejects_empty_field(self):
        with pytest.raises(ValueError):
            GovernanceLogRedactionRule(field="")

    def test_default_replacement(self):
        rule = GovernanceLogRedactionRule(field="token")

        assert rule.replacement == DEFAULT_REDACTION_REPLACEMENT

    def test_to_dict(self):
        rule = GovernanceLogRedactionRule(
            field="token", replacement="[GONE]"
        )

        assert rule.to_dict() == {
            "field": "token",
            "replacement": "[GONE]",
        }


class TestGovernanceLogRedactionServiceDefaults:

    def test_default_rules_registered(self):
        service = GovernanceLogRedactionService()

        fields = {rule.field for rule in service.list_rules()}

        assert fields == set(DEFAULT_REDACTED_FIELDS)

    @pytest.mark.parametrize("field", DEFAULT_REDACTED_FIELDS)
    def test_default_field_is_redacted(self, field):
        service = GovernanceLogRedactionService()

        entry = _entry(**{field: "sensitive-value"})

        redacted = service.redact(entry)

        assert redacted.fields[field] == DEFAULT_REDACTION_REPLACEMENT

    def test_non_sensitive_field_is_untouched(self):
        service = GovernanceLogRedactionService()

        entry = _entry(username="alice", dispatch_id="d1")

        redacted = service.redact(entry)

        assert redacted.fields == {
            "username": "alice",
            "dispatch_id": "d1",
        }

    def test_case_insensitive_matching(self):
        service = GovernanceLogRedactionService()

        entry = _entry(Password="hunter2", TOKEN="abc")

        redacted = service.redact(entry)

        assert redacted.fields["Password"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert redacted.fields["TOKEN"] == DEFAULT_REDACTION_REPLACEMENT

    def test_redact_does_not_mutate_original_entry(self):
        service = GovernanceLogRedactionService()

        entry = _entry(password="hunter2")

        service.redact(entry)

        assert entry.fields["password"] == "hunter2"

    def test_redact_preserves_non_fields_attributes(self):
        service = GovernanceLogRedactionService()

        entry = _entry(password="hunter2")

        redacted = service.redact(entry)

        assert redacted.timestamp == entry.timestamp
        assert redacted.level == entry.level
        assert redacted.component == entry.component
        assert redacted.event == entry.event


class TestGovernanceLogRedactionServiceNestedRedaction:

    def test_redacts_nested_dict_value(self):
        service = GovernanceLogRedactionService()

        entry = _entry(
            headers={"Authorization": "Bearer abc", "Accept": "*/*"}
        )

        redacted = service.redact(entry)

        assert redacted.fields["headers"]["Authorization"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert redacted.fields["headers"]["Accept"] == "*/*"

    def test_redacts_deeply_nested_dict_value(self):
        service = GovernanceLogRedactionService()

        entry = _entry(
            request={"auth": {"token": "abc"}, "path": "/x"}
        )

        redacted = service.redact(entry)

        assert redacted.fields["request"]["auth"]["token"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert redacted.fields["request"]["path"] == "/x"

    def test_redacts_dicts_nested_in_lists(self):
        service = GovernanceLogRedactionService()

        entry = _entry(
            items=[{"token": "abc"}, {"username": "alice"}]
        )

        redacted = service.redact(entry)

        assert redacted.fields["items"][0]["token"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert redacted.fields["items"][1]["username"] == "alice"

    def test_preserves_structure_of_unredacted_nested_values(self):
        service = GovernanceLogRedactionService()

        entry = _entry(
            metadata={"count": 3, "tags": ["a", "b"]}
        )

        redacted = service.redact(entry)

        assert redacted.fields["metadata"] == {
            "count": 3,
            "tags": ["a", "b"],
        }


class TestGovernanceLogRedactionServiceCustomRules:

    def test_register_adds_custom_rule(self):
        service = GovernanceLogRedactionService()

        service.register(
            GovernanceLogRedactionRule(
                field="ssn", replacement="[SSN]"
            )
        )

        entry = _entry(ssn="123-45-6789")

        redacted = service.redact(entry)

        assert redacted.fields["ssn"] == "[SSN]"

    def test_register_can_override_replacement_for_default_field(
        self,
    ):
        service = GovernanceLogRedactionService()

        service.register(
            GovernanceLogRedactionRule(
                field="password", replacement="[GONE]"
            )
        )

        entry = _entry(password="hunter2")

        redacted = service.redact(entry)

        assert redacted.fields["password"] == "[GONE]"

    def test_register_duplicate_field_replaces_not_duplicates(self):
        service = GovernanceLogRedactionService()

        service.register(
            GovernanceLogRedactionRule(field="ssn", replacement="[A]")
        )
        service.register(
            GovernanceLogRedactionRule(field="SSN", replacement="[B]")
        )

        matching_rules = [
            rule
            for rule in service.list_rules()
            if rule.field.lower() == "ssn"
        ]

        assert len(matching_rules) == 1
        assert matching_rules[0].replacement == "[B]"

    def test_unregister_removes_rule(self):
        service = GovernanceLogRedactionService()

        service.unregister("password")

        entry = _entry(password="hunter2")

        redacted = service.redact(entry)

        assert redacted.fields["password"] == "hunter2"

    def test_unregister_unknown_field_is_a_no_op(self):
        service = GovernanceLogRedactionService()

        service.unregister("does-not-exist")

        assert len(service.list_rules()) == len(
            DEFAULT_REDACTED_FIELDS
        )

    def test_constructor_accepts_custom_rules(self):
        service = GovernanceLogRedactionService(
            rules=[
                GovernanceLogRedactionRule(
                    field="ssn", replacement="[SSN]"
                )
            ]
        )

        fields = {rule.field for rule in service.list_rules()}

        assert "ssn" in fields
        assert "password" in fields


class TestGovernanceLogRedactionServiceIdempotency:

    def test_redacting_twice_is_stable(self):
        service = GovernanceLogRedactionService()

        entry = _entry(password="hunter2", username="alice")

        once = service.redact(entry)
        twice = service.redact(once)

        assert once.fields == twice.fields
        assert twice.fields["password"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )


class TestGovernanceLogRedactionLoggerIntegration:

    def test_logger_redacts_before_buffering(self):
        redaction_service = GovernanceLogRedactionService()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            redaction_service=redaction_service,
        )

        entry = logger.info(
            "metrics", "record_success", password="hunter2"
        )

        assert entry.fields["password"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert logger.entries()[0].fields["password"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )

    def test_logger_redacts_before_repository_write_through(self):
        repository = InMemoryGovernanceLogRepository()

        redaction_service = GovernanceLogRedactionService()

        logger = GovernanceIntegrityLogger(
            clock=lambda: BASE_TIME,
            repository=repository,
            redaction_service=redaction_service,
        )

        logger.info("metrics", "record_success", token="abc123")

        [stored] = repository.list()

        assert stored.fields["token"] == DEFAULT_REDACTION_REPLACEMENT

    def test_set_redaction_service_attaches_after_construction(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        logger.set_redaction_service(GovernanceLogRedactionService())

        entry = logger.info(
            "metrics", "record_success", secret="s3cr3t"
        )

        assert entry.fields["secret"] == DEFAULT_REDACTION_REPLACEMENT

    def test_logger_without_redaction_service_is_unaffected(self):
        logger = GovernanceIntegrityLogger(clock=lambda: BASE_TIME)

        entry = logger.info(
            "metrics", "record_success", password="hunter2"
        )

        assert entry.fields["password"] == "hunter2"


class TestGovernanceLogRedactionExportIntegration:

    def _export_service(
        self, repository, redaction_service=None
    ) -> GovernanceLogExportService:
        return GovernanceLogExportService(
            GovernanceLogSearchService(repository),
            redaction_service=redaction_service,
        )

    def test_export_redacts_entries_written_without_a_logger(self):
        # Entry appended directly to the repository, bypassing the
        # logger entirely -- simulates data persisted before
        # redaction was configured, or by a caller that skipped it.
        repository = InMemoryGovernanceLogRepository()
        repository.append(_entry(password="hunter2"))

        service = self._export_service(
            repository, GovernanceLogRedactionService()
        )

        stream = StringIO()

        service.export_ndjson(stream)

        payload = json.loads(stream.getvalue().strip())

        assert payload["fields"]["password"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )

    def test_export_json_redacts(self):
        repository = InMemoryGovernanceLogRepository()
        repository.append(_entry(token="abc123"))

        service = self._export_service(
            repository, GovernanceLogRedactionService()
        )

        stream = StringIO()

        service.export_json(stream)

        payload = json.loads(stream.getvalue())

        assert payload[0]["fields"]["token"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )

    def test_export_csv_redacts(self):
        repository = InMemoryGovernanceLogRepository()
        repository.append(_entry(secret="s3cr3t"))

        service = self._export_service(
            repository, GovernanceLogRedactionService()
        )

        stream = StringIO()

        service.export_csv(stream)

        import csv

        reader = csv.DictReader(StringIO(stream.getvalue()))

        row = next(reader)

        assert json.loads(row["fields_json"])["secret"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )

    def test_export_without_redaction_service_leaves_values_intact(
        self,
    ):
        repository = InMemoryGovernanceLogRepository()
        repository.append(_entry(password="hunter2"))

        service = self._export_service(repository, None)

        stream = StringIO()

        service.export_ndjson(stream)

        payload = json.loads(stream.getvalue().strip())

        assert payload["fields"]["password"] == "hunter2"


class TestGovernanceLogRedactionCli:

    def _stub_runtime(self, redaction_service):
        class _StubRuntime:
            def build_integrity_log_redaction_service(self):
                return redaction_service

        return _StubRuntime()

    def test_rules_runner_lists_default_rules(self, monkeypatch):
        redaction_service = GovernanceLogRedactionService()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(redaction_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_redaction_rules(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        fields = {item["field"] for item in payload}

        assert fields == set(DEFAULT_REDACTED_FIELDS)

    def test_rules_runner_reflects_custom_registration(
        self, monkeypatch
    ):
        redaction_service = GovernanceLogRedactionService()
        redaction_service.register(
            GovernanceLogRedactionRule(field="ssn")
        )

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(redaction_service),
        )

        stdout = StringIO()

        run_deployment_governance_logging_redaction_rules(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        payload = json.loads(stdout.getvalue())

        assert "ssn" in {item["field"] for item in payload}

    def test_test_runner_shows_before_and_after(self, monkeypatch):
        redaction_service = GovernanceLogRedactionService()

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: self._stub_runtime(redaction_service),
        )

        stdout = StringIO()

        exit_code = run_deployment_governance_logging_redaction_test(
            json_output=True, stdout=stdout, stderr=StringIO()
        )

        assert exit_code == 0

        payload = json.loads(stdout.getvalue())

        assert payload["before"]["password"] != (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert payload["after"]["password"] == (
            DEFAULT_REDACTION_REPLACEMENT
        )
        assert payload["after"]["username"] == "alice"

    def test_test_runner_never_persists_anything(self, monkeypatch):
        repository = InMemoryGovernanceLogRepository()

        redaction_service = GovernanceLogRedactionService()

        class _StubRuntime:
            def build_integrity_log_redaction_service(self):
                return redaction_service

        monkeypatch.setattr(
            "backend.observability.deployment_governance_logging_cli"
            ".build_deployment_governance_persistence",
            lambda config: _StubRuntime(),
        )

        run_deployment_governance_logging_redaction_test(
            stdout=StringIO(), stderr=StringIO()
        )

        assert repository.list() == ()
