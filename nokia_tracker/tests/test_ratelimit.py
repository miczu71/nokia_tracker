import pytest

from nokia_tracker import ratelimit


@pytest.fixture(autouse=True)
def _reset_breaker():
    # Circuit breaker jest w pamięci procesu (współdzielony między testami)
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()
    yield
    ratelimit._consecutive_failures.clear()
    ratelimit._opened_at.clear()


# --- token bucket ---

def test_calls_today_zero_when_no_calls(conn):
    assert ratelimit.calls_today(conn, "yahoo") == 0


def test_record_call_increments(conn):
    ratelimit.record_call(conn, "yahoo")
    ratelimit.record_call(conn, "yahoo")
    assert ratelimit.calls_today(conn, "yahoo") == 2


def test_allow_respects_daily_limit(conn):
    for _ in range(3):
        ratelimit.record_call(conn, "finnhub")
    assert ratelimit.allow(conn, "finnhub", max_per_day=3) is False
    assert ratelimit.allow(conn, "finnhub", max_per_day=5) is True


def test_allow_zero_means_unlimited(conn):
    for _ in range(100):
        ratelimit.record_call(conn, "yahoo")
    assert ratelimit.allow(conn, "yahoo", max_per_day=0) is True


def test_calls_today_isolated_per_provider(conn):
    ratelimit.record_call(conn, "yahoo")
    assert ratelimit.calls_today(conn, "finnhub") == 0


# --- circuit breaker ---

def test_provider_status_ok_initially():
    assert ratelimit.provider_status("yahoo") == "ok"


def test_provider_status_degraded_after_one_failure():
    ratelimit.record_failure("yahoo")
    assert ratelimit.provider_status("yahoo") == "degraded"


def test_provider_status_down_after_three_failures():
    for _ in range(3):
        ratelimit.record_failure("yahoo")
    assert ratelimit.provider_status("yahoo") == "down"
    assert ratelimit.is_circuit_open("yahoo") is True


def test_success_resets_failure_count():
    for _ in range(3):
        ratelimit.record_failure("yahoo")
    ratelimit.record_success("yahoo")
    assert ratelimit.provider_status("yahoo") == "ok"


def test_circuit_stays_open_before_cooldown_elapses(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    for _ in range(3):
        ratelimit.record_failure("local")
    t[0] += ratelimit._CIRCUIT_COOLDOWN_SECONDS - 1
    assert ratelimit.is_circuit_open("local") is True


def test_circuit_closes_after_cooldown_elapses(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    for _ in range(3):
        ratelimit.record_failure("local")
    assert ratelimit.is_circuit_open("local") is True
    t[0] += ratelimit._CIRCUIT_COOLDOWN_SECONDS
    assert ratelimit.is_circuit_open("local") is False
    # reset licznika porażek, nie tylko zamknięcie obwodu na ten jeden odczyt
    assert ratelimit.provider_status("local") == "ok"


# --- backoff_retry ---

class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def test_backoff_retry_succeeds_first_try():
    calls = []

    def fn():
        calls.append(1)
        return _Resp(200)

    resp = ratelimit.backoff_retry(fn, provider="yahoo", max_attempts=3, base_delay=0.001)
    assert resp.status_code == 200
    assert len(calls) == 1
    assert ratelimit.provider_status("yahoo") == "ok"


def test_backoff_retry_retries_on_429_then_succeeds():
    statuses = [429, 429, 200]
    calls = []

    def fn():
        calls.append(1)
        return _Resp(statuses[len(calls) - 1])

    resp = ratelimit.backoff_retry(fn, provider="yahoo", max_attempts=3, base_delay=0.001)
    assert resp.status_code == 200
    assert len(calls) == 3


def test_backoff_retry_exhausts_and_records_failure():
    def fn():
        return _Resp(502)

    resp = ratelimit.backoff_retry(fn, provider="freellmapi", max_attempts=2, base_delay=0.001)
    assert resp.status_code == 502
    assert ratelimit.provider_status("freellmapi") == "degraded"


def test_backoff_retry_non_retryable_status_returns_immediately():
    calls = []

    def fn():
        calls.append(1)
        return _Resp(404)

    resp = ratelimit.backoff_retry(fn, provider="yahoo", max_attempts=3, base_delay=0.001)
    assert resp.status_code == 404
    assert len(calls) == 1  # 404 nie jest w retryable_statuses -> bez ponawiania
