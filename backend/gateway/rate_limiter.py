from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

STRATEGIES = frozenset({"fixed_window", "sliding_window", "token_bucket"})


class InvalidStrategyError(ValueError):
    pass


class RateLimitRuleAlreadyRegisteredError(ValueError):
    pass


class UnknownRateLimitClientError(KeyError):
    pass


class RateLimitExceededError(RuntimeError):
    def __init__(self, client: str, retry_after: float) -> None:
        super().__init__(f"rate limit exceeded for {client}, retry after {retry_after:.3f}s")
        self.client = client
        self.retry_after = retry_after


@dataclass(frozen=True)
class RateLimitRule:
    """A configured quota for a single client."""

    client: str
    strategy: str
    limit: int
    window_seconds: float
    burst: int = 0

    def to_dict(self) -> dict:
        return {
            "client": self.client,
            "strategy": self.strategy,
            "limit": self.limit,
            "window_seconds": self.window_seconds,
            "burst": self.burst,
        }


@dataclass(frozen=True)
class RateLimitState:
    """A point-in-time snapshot of a client's quota."""

    client: str
    strategy: str
    limit: int
    remaining: float
    allowed: bool
    retry_after: float = 0.0

    def to_dict(self) -> dict:
        return {
            "client": self.client,
            "strategy": self.strategy,
            "limit": self.limit,
            "remaining": self.remaining,
            "allowed": self.allowed,
            "retry_after": self.retry_after,
        }


class RateLimiter:
    """Throttles requests per client using a configurable strategy."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._rules: dict = {}
        self._state: dict = {}
        self._lock = Lock()
        self._clock = clock

    def configure(
        self,
        client: str,
        strategy: str,
        limit: int,
        window_seconds: float,
        *,
        burst: int = 0,
    ) -> RateLimitRule:
        if not client:
            raise ValueError("client is required")
        if strategy not in STRATEGIES:
            raise InvalidStrategyError(f"unsupported strategy: {strategy}")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if burst < 0:
            raise ValueError("burst must not be negative")
        with self._lock:
            if client in self._rules:
                raise RateLimitRuleAlreadyRegisteredError(f"{client} already has a configured rule")
            rule = RateLimitRule(
                client=client, strategy=strategy, limit=limit, window_seconds=window_seconds, burst=burst
            )
            self._rules[client] = rule
            self._state[client] = self._initial_state(strategy)
        return rule

    def _initial_state(self, strategy: str) -> dict:
        now = self._clock()
        if strategy == "fixed_window":
            return {"window_start": now, "count": 0}
        if strategy == "sliding_window":
            return {"events": []}
        return {"tokens": None, "last_refill": now}

    def _require_rule(self, client: str) -> RateLimitRule:
        rule = self._rules.get(client)
        if rule is None:
            raise UnknownRateLimitClientError(client)
        return rule

    def _evaluate(self, rule: RateLimitRule, cost: int, mutate: bool):
        if rule.strategy == "fixed_window":
            return self._evaluate_fixed_window(rule, cost, mutate)
        if rule.strategy == "sliding_window":
            return self._evaluate_sliding_window(rule, cost, mutate)
        return self._evaluate_token_bucket(rule, cost, mutate)

    def _evaluate_fixed_window(self, rule: RateLimitRule, cost: int, mutate: bool):
        state = self._state[rule.client]
        now = self._clock()
        capacity = rule.limit + rule.burst
        elapsed = now - state["window_start"]
        if elapsed >= rule.window_seconds:
            rollovers = int(elapsed // rule.window_seconds)
            window_start = state["window_start"] + rollovers * rule.window_seconds
            count = 0
        else:
            window_start = state["window_start"]
            count = state["count"]
        remaining_before = max(capacity - count, 0)
        allowed = count + cost <= capacity
        retry_after = 0.0 if allowed else max((window_start + rule.window_seconds) - now, 0.0)
        if mutate:
            state["window_start"] = window_start
            state["count"] = count + cost if allowed else count
        return allowed, remaining_before, retry_after

    def _evaluate_sliding_window(self, rule: RateLimitRule, cost: int, mutate: bool):
        state = self._state[rule.client]
        now = self._clock()
        capacity = rule.limit + rule.burst
        window_start = now - rule.window_seconds
        events = [(ts, c) for ts, c in state["events"] if ts > window_start]
        total = sum(c for _, c in events)
        remaining_before = max(capacity - total, 0)
        allowed = total + cost <= capacity
        retry_after = 0.0
        if not allowed and events:
            oldest_ts, _ = events[0]
            retry_after = max((oldest_ts + rule.window_seconds) - now, 0.0)
        if mutate:
            if allowed and cost > 0:
                events.append((now, cost))
            state["events"] = events
        return allowed, remaining_before, retry_after

    def _evaluate_token_bucket(self, rule: RateLimitRule, cost: int, mutate: bool):
        state = self._state[rule.client]
        now = self._clock()
        capacity = rule.limit + rule.burst
        refill_rate = rule.limit / rule.window_seconds
        tokens = state["tokens"]
        if tokens is None:
            tokens = capacity
        elapsed = now - state["last_refill"]
        tokens = min(capacity, tokens + elapsed * refill_rate)
        allowed = tokens >= cost
        retry_after = 0.0 if allowed else (cost - tokens) / refill_rate
        if mutate:
            state["last_refill"] = now
            state["tokens"] = tokens - cost if allowed else tokens
        return allowed, tokens, retry_after

    def allow(self, client: str, cost: int = 1) -> bool:
        with self._lock:
            rule = self._require_rule(client)
            allowed, _, _ = self._evaluate(rule, cost, mutate=False)
            return allowed

    def consume(self, client: str, cost: int = 1) -> RateLimitState:
        with self._lock:
            rule = self._require_rule(client)
            allowed, _, retry_after = self._evaluate(rule, cost, mutate=True)
            if not allowed:
                raise RateLimitExceededError(client, retry_after)
            _, remaining_after, _ = self._evaluate(rule, 0, mutate=False)
            return RateLimitState(
                client=client,
                strategy=rule.strategy,
                limit=rule.limit,
                remaining=remaining_after,
                allowed=True,
                retry_after=0.0,
            )

    def status(self, client: str, cost: int = 1) -> RateLimitState:
        with self._lock:
            rule = self._require_rule(client)
            allowed, remaining, retry_after = self._evaluate(rule, cost, mutate=False)
            return RateLimitState(
                client=client,
                strategy=rule.strategy,
                limit=rule.limit,
                remaining=remaining,
                allowed=allowed,
                retry_after=retry_after,
            )

    def reset(self, client: str) -> None:
        with self._lock:
            rule = self._require_rule(client)
            self._state[client] = self._initial_state(rule.strategy)


_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _rate_limiter


router = APIRouter(prefix="/gateway/rate-limit", tags=["gateway-rate-limit"])


@router.post("", status_code=201)
def configure_rate_limit_endpoint(
    payload: dict = Body(default={}),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict:
    try:
        rule = limiter.configure(
            payload.get("client", ""),
            payload.get("strategy", ""),
            payload.get("limit", 0),
            payload.get("window_seconds", 0),
            burst=payload.get("burst", 0),
        )
    except RateLimitRuleAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, InvalidStrategyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return rule.to_dict()


@router.get("/{client}")
def rate_limit_status_endpoint(
    client: str,
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> dict:
    try:
        state = limiter.status(client)
    except UnknownRateLimitClientError:
        raise HTTPException(status_code=404, detail="no rate limit rule configured for client")
    return state.to_dict()


@router.delete("/{client}", status_code=204)
def reset_rate_limit_endpoint(
    client: str,
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> None:
    try:
        limiter.reset(client)
    except UnknownRateLimitClientError:
        raise HTTPException(status_code=404, detail="no rate limit rule configured for client")
