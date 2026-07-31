import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.gateway.rate_limiter import (
    InvalidStrategyError,
    RateLimitExceededError,
    RateLimiter,
    RateLimitRule,
    RateLimitRuleAlreadyRegisteredError,
    RateLimitState,
    UnknownRateLimitClientError,
    get_rate_limiter,
    router as rate_limiter_router,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock) -> RateLimiter:
    return RateLimiter(clock=clock)


@pytest.fixture
def client(limiter: RateLimiter) -> TestClient:
    app = FastAPI()
    app.include_router(rate_limiter_router)
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    return TestClient(app)


def test_configure_creates_rule(limiter: RateLimiter):
    rule = limiter.configure("alice", "fixed_window", limit=5, window_seconds=60)

    assert isinstance(rule, RateLimitRule)
    assert rule.client == "alice"
    assert rule.limit == 5


def test_configure_rejects_unknown_strategy(limiter: RateLimiter):
    with pytest.raises(InvalidStrategyError):
        limiter.configure("alice", "leaky_bucket", limit=5, window_seconds=60)


def test_configure_rejects_duplicate_client(limiter: RateLimiter):
    limiter.configure("alice", "fixed_window", limit=5, window_seconds=60)

    with pytest.raises(RateLimitRuleAlreadyRegisteredError):
        limiter.configure("alice", "fixed_window", limit=5, window_seconds=60)


def test_configure_rejects_non_positive_limit(limiter: RateLimiter):
    with pytest.raises(ValueError):
        limiter.configure("alice", "fixed_window", limit=0, window_seconds=60)


def test_allow_true_within_quota(limiter: RateLimiter):
    limiter.configure("alice", "fixed_window", limit=2, window_seconds=60)

    assert limiter.allow("alice") is True


def test_allow_unknown_client_raises(limiter: RateLimiter):
    with pytest.raises(UnknownRateLimitClientError):
        limiter.allow("does-not-exist")


# --- Fixed window ---


def test_fixed_window_enforces_limit(limiter: RateLimiter, clock: FakeClock):
    limiter.configure("alice", "fixed_window", limit=2, window_seconds=10)

    limiter.consume("alice")
    limiter.consume("alice")

    with pytest.raises(RateLimitExceededError):
        limiter.consume("alice")


def test_fixed_window_resets_after_window_elapses(limiter: RateLimiter, clock: FakeClock):
    limiter.configure("alice", "fixed_window", limit=1, window_seconds=10)
    limiter.consume("alice")

    clock.advance(10)

    state = limiter.consume("alice")
    assert state.allowed is True


def test_fixed_window_allows_burst_allowance(limiter: RateLimiter):
    limiter.configure("alice", "fixed_window", limit=1, window_seconds=10, burst=1)

    limiter.consume("alice")
    state = limiter.consume("alice")

    assert state.allowed is True


def test_fixed_window_retry_after_reported_on_exceeded(limiter: RateLimiter, clock: FakeClock):
    limiter.configure("alice", "fixed_window", limit=1, window_seconds=10)
    limiter.consume("alice")

    with pytest.raises(RateLimitExceededError) as exc_info:
        limiter.consume("alice")

    assert exc_info.value.retry_after == pytest.approx(10.0)


# --- Sliding window ---


def test_sliding_window_enforces_limit(limiter: RateLimiter):
    limiter.configure("bob", "sliding_window", limit=2, window_seconds=10)

    limiter.consume("bob")
    limiter.consume("bob")

    with pytest.raises(RateLimitExceededError):
        limiter.consume("bob")


def test_sliding_window_allows_after_events_age_out(limiter: RateLimiter, clock: FakeClock):
    limiter.configure("bob", "sliding_window", limit=1, window_seconds=10)
    limiter.consume("bob")

    clock.advance(10.001)

    state = limiter.consume("bob")
    assert state.allowed is True


def test_sliding_window_partial_recovery(limiter: RateLimiter, clock: FakeClock):
    limiter.configure("bob", "sliding_window", limit=2, window_seconds=10)
    limiter.consume("bob")
    clock.advance(5)
    limiter.consume("bob")

    with pytest.raises(RateLimitExceededError):
        limiter.consume("bob")

    clock.advance(5.001)

    state = limiter.consume("bob")
    assert state.allowed is True


# --- Token bucket ---


def test_token_bucket_starts_full(limiter: RateLimiter):
    limiter.configure("carol", "token_bucket", limit=3, window_seconds=10)

    state = limiter.status("carol", cost=3)
    assert state.allowed is True


def test_token_bucket_enforces_capacity(limiter: RateLimiter):
    limiter.configure("carol", "token_bucket", limit=2, window_seconds=10)

    limiter.consume("carol")
    limiter.consume("carol")

    with pytest.raises(RateLimitExceededError):
        limiter.consume("carol")


def test_token_bucket_refills_over_time(limiter: RateLimiter, clock: FakeClock):
    limiter.configure("carol", "token_bucket", limit=2, window_seconds=10)
    limiter.consume("carol")
    limiter.consume("carol")

    clock.advance(5)

    state = limiter.consume("carol")
    assert state.allowed is True


def test_token_bucket_burst_capacity(limiter: RateLimiter):
    limiter.configure("carol", "token_bucket", limit=1, window_seconds=10, burst=2)

    limiter.consume("carol")
    limiter.consume("carol")
    state = limiter.consume("carol")

    assert state.allowed is True


# --- status / reset ---


def test_status_reports_remaining_without_consuming(limiter: RateLimiter):
    limiter.configure("alice", "fixed_window", limit=2, window_seconds=10)
    limiter.consume("alice")

    state = limiter.status("alice")

    assert isinstance(state, RateLimitState)
    assert state.remaining == 1
    second_status = limiter.status("alice")
    assert second_status.remaining == 1


def test_status_unknown_client_raises(limiter: RateLimiter):
    with pytest.raises(UnknownRateLimitClientError):
        limiter.status("does-not-exist")


def test_reset_clears_state(limiter: RateLimiter):
    limiter.configure("alice", "fixed_window", limit=1, window_seconds=10)
    limiter.consume("alice")

    limiter.reset("alice")

    state = limiter.consume("alice")
    assert state.allowed is True


def test_reset_unknown_client_raises(limiter: RateLimiter):
    with pytest.raises(UnknownRateLimitClientError):
        limiter.reset("does-not-exist")


# --- API ---


def test_api_configure_and_status(client: TestClient):
    response = client.post(
        "/gateway/rate-limit",
        json={"client": "alice", "strategy": "fixed_window", "limit": 2, "window_seconds": 10},
    )
    assert response.status_code == 201
    assert response.json()["client"] == "alice"

    status_response = client.get("/gateway/rate-limit/alice")
    assert status_response.status_code == 200
    assert status_response.json()["remaining"] == 2


def test_api_configure_duplicate_returns_409(client: TestClient):
    client.post(
        "/gateway/rate-limit",
        json={"client": "alice", "strategy": "fixed_window", "limit": 2, "window_seconds": 10},
    )
    response = client.post(
        "/gateway/rate-limit",
        json={"client": "alice", "strategy": "fixed_window", "limit": 2, "window_seconds": 10},
    )

    assert response.status_code == 409


def test_api_configure_invalid_strategy_returns_422(client: TestClient):
    response = client.post(
        "/gateway/rate-limit",
        json={"client": "alice", "strategy": "leaky_bucket", "limit": 2, "window_seconds": 10},
    )

    assert response.status_code == 422


def test_api_status_unknown_client_returns_404(client: TestClient):
    response = client.get("/gateway/rate-limit/does-not-exist")

    assert response.status_code == 404


def test_api_delete_resets_client(client: TestClient):
    client.post(
        "/gateway/rate-limit",
        json={"client": "alice", "strategy": "fixed_window", "limit": 1, "window_seconds": 10},
    )
    client.get("/gateway/rate-limit/alice")

    response = client.delete("/gateway/rate-limit/alice")
    assert response.status_code == 204


def test_api_delete_unknown_client_returns_404(client: TestClient):
    response = client.delete("/gateway/rate-limit/does-not-exist")

    assert response.status_code == 404
