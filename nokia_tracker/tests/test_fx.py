from nokia_tracker import fx, quotes
from nokia_tracker.providers.base import QuoteProviderError


def test_refresh_eurpln_yahoo_success_skips_ecb(conn, monkeypatch):
    iid = quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    ecb_calls = []
    monkeypatch.setattr("nokia_tracker.fx.quotes.refresh_recent_daily", lambda *a, **kw: 5)
    monkeypatch.setattr("nokia_tracker.fx.fx_ecb.fetch_rate",
                        lambda *a, **kw: ecb_calls.append(1))

    fx.refresh_eurpln(conn, iid)
    assert len(ecb_calls) == 0


def test_refresh_eurpln_falls_back_to_ecb_on_yahoo_error(conn, monkeypatch):
    iid = quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")

    def raise_error(*a, **kw):
        raise QuoteProviderError("Yahoo padł")

    monkeypatch.setattr("nokia_tracker.fx.quotes.refresh_recent_daily", raise_error)
    monkeypatch.setattr("nokia_tracker.fx.fx_ecb.fetch_rate",
                        lambda *a, **kw: (4.31, "2026-07-24"))

    fx.refresh_eurpln(conn, iid)
    latest = quotes.latest_quote(conn, iid, granularity="daily")
    assert latest["close"] == 4.31
    assert latest["source"] == "ecb"


def test_refresh_eurpln_falls_back_to_ecb_on_zero_candles(conn, monkeypatch):
    # Yahoo nie rzuca wyjątku, ale zwraca 0 świec (np. pusty wynik) -> to
    # też powinno wywołać fallback, nie ciche "sukces bez danych".
    iid = quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    monkeypatch.setattr("nokia_tracker.fx.quotes.refresh_recent_daily", lambda *a, **kw: 0)
    monkeypatch.setattr("nokia_tracker.fx.fx_ecb.fetch_rate",
                        lambda *a, **kw: (4.31, "2026-07-24"))

    fx.refresh_eurpln(conn, iid)
    latest = quotes.latest_quote(conn, iid, granularity="daily")
    assert latest["close"] == 4.31


def test_refresh_eurpln_both_sources_fail_leaves_no_data(conn, monkeypatch):
    iid = quotes.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")

    def raise_error(*a, **kw):
        raise QuoteProviderError("Yahoo padł")

    monkeypatch.setattr("nokia_tracker.fx.quotes.refresh_recent_daily", raise_error)
    monkeypatch.setattr("nokia_tracker.fx.fx_ecb.fetch_rate", lambda *a, **kw: None)

    fx.refresh_eurpln(conn, iid)  # nie rzuca — sensor po prostu zostanie 'unknown'
    assert quotes.latest_quote(conn, iid) is None
