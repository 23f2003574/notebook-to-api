from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .deployment_governance_logging import GovernanceLogEntry

DEFAULT_REDACTION_REPLACEMENT: str = "***REDACTED***"

DEFAULT_REDACTED_FIELDS: tuple[str, ...] = (
    "password",
    "token",
    "secret",
    "api_key",
    "authorization",
    "cookie",
)


@dataclass(frozen=True)
class GovernanceLogRedactionRule:
    """
    One rule telling GovernanceLogRedactionService to replace the
    value of any fields-mapping key matching `field`
    (case-insensitive) with `replacement`, wherever it appears --
    including nested inside dicts and lists.
    """

    field: str

    replacement: str = DEFAULT_REDACTION_REPLACEMENT

    def __post_init__(self) -> None:
        if not self.field:
            raise ValueError(
                "field must not be empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "replacement": self.replacement,
        }


class GovernanceLogRedactionService:
    """
    Redacts sensitive values out of a GovernanceLogEntry's
    structured fields before it is persisted or exported.

    Matching is case-insensitive and recursive: a rule for "token"
    matches a top-level fields key "token", a differently-cased key
    "Token", and a "token" key nested arbitrarily deep inside dicts
    or lists held in fields (e.g. fields={"headers": {"Authorization":
    "..."}}). Only the matching key's value is replaced; every other
    key, and the entry's timestamp/level/component/event, are left
    untouched, so log structure is preserved. Redacting an
    already-redacted entry is a no-op beyond replacing the same
    value with the same replacement again: redact() is idempotent.

    A fixed set of common secret-shaped field names (password,
    token, secret, api_key, authorization, cookie) is registered by
    default; register()/unregister() customize the rule set without
    recreating the service.
    """

    def __init__(
        self,
        *,
        rules: Iterable[GovernanceLogRedactionRule] | None = None,
    ) -> None:
        self._lock = Lock()

        self._rules: dict[str, GovernanceLogRedactionRule] = {}

        for field in DEFAULT_REDACTED_FIELDS:
            self._rules[field] = GovernanceLogRedactionRule(
                field=field
            )

        if rules is not None:
            for rule in rules:
                self._rules[rule.field.lower()] = rule

    def register(self, rule: GovernanceLogRedactionRule) -> None:
        """
        Add a redaction rule for rule.field (case-insensitive).

        Registering a field that already has a rule replaces it
        rather than creating a duplicate: list_rules() never returns
        two rules for the same field.
        """

        with self._lock:
            self._rules[rule.field.lower()] = rule

    def unregister(self, field: str) -> None:
        """
        Remove the redaction rule for field, if one is registered.
        A no-op if it is not (including the default rules, which
        can be removed the same way as custom ones).
        """

        with self._lock:
            self._rules.pop(field.lower(), None)

    def list_rules(self) -> tuple[GovernanceLogRedactionRule, ...]:
        """
        Return every currently registered rule, in no particular
        order.
        """

        with self._lock:
            return tuple(self._rules.values())

    def redact(
        self,
        entry: "GovernanceLogEntry",
    ) -> "GovernanceLogEntry":
        """
        Return a new GovernanceLogEntry with every fields value
        whose key matches a registered rule (at any depth) replaced
        by that rule's replacement. entry itself is never mutated
        (GovernanceLogEntry is frozen).
        """

        from .deployment_governance_logging import GovernanceLogEntry

        with self._lock:
            rules = dict(self._rules)

        return GovernanceLogEntry(
            timestamp=entry.timestamp,
            level=entry.level,
            component=entry.component,
            event=entry.event,
            fields=_redact_mapping(entry.fields, rules),
        )


def _redact_mapping(
    mapping: Mapping[str, Any],
    rules: Mapping[str, GovernanceLogRedactionRule],
) -> dict[str, Any]:
    redacted: dict[str, Any] = {}

    for key, value in mapping.items():
        rule = (
            rules.get(key.lower())
            if isinstance(key, str)
            else None
        )

        redacted[key] = (
            rule.replacement
            if rule is not None
            else _redact_value(value, rules)
        )

    return redacted


def _redact_value(
    value: Any,
    rules: Mapping[str, GovernanceLogRedactionRule],
) -> Any:
    if isinstance(value, Mapping):
        return _redact_mapping(value, rules)

    if isinstance(value, (list, tuple)):
        return type(value)(
            _redact_value(item, rules) for item in value
        )

    return value
