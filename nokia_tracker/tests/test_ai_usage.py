"""Licznik wywołań AI per provider (krok 29: budżet oddzielny dla darmowego
'local' i płatnych 'gemini'/'anthropic' — patrz test_provider.py dla
zachowania łańcucha, tu tylko surowe liczniki tabeli ai_usage)."""
from nokia_tracker.ai import usage


def test_calls_today_filters_by_provider(conn):
    usage.record_call(conn, "local", "model-a", "score_news", 10)
    usage.record_call(conn, "local", "model-a", "score_news", 10)
    usage.record_call(conn, "gemini", "model-b", "score_news", 5)

    assert usage.calls_today(conn, "local") == 2
    assert usage.calls_today(conn, "gemini") == 1
    assert usage.calls_today(conn, "anthropic") == 0


def test_calls_today_without_provider_sums_all(conn):
    usage.record_call(conn, "local", "model-a", "score_news", 10)
    usage.record_call(conn, "gemini", "model-b", "score_news", 5)
    assert usage.calls_today(conn) == 2  # zachowanie sprzed kroku 29, dla sensors.py


def test_tokens_today_filters_by_provider(conn):
    usage.record_call(conn, "local", "model-a", "score_news", 100)
    usage.record_call(conn, "gemini", "model-b", "score_news", 40)
    assert usage.tokens_today(conn, "local") == 100
    assert usage.tokens_today(conn, "gemini") == 40
    assert usage.tokens_today(conn) == 140


def test_allow_is_scoped_per_provider(conn):
    usage.record_call(conn, "local", "model-a", "score_news", 1)
    assert usage.allow(conn, "local", 1) is False
    assert usage.allow(conn, "gemini", 1) is True  # gemini nie tknięty, ma własną pulę


def test_allow_zero_means_unlimited(conn):
    for _ in range(5):
        usage.record_call(conn, "local", "model-a", "score_news", 1)
    assert usage.allow(conn, "local", 0) is True
