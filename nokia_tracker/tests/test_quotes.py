from nokia_tracker import quotes
from nokia_tracker.models import Candle
from nokia_tracker.providers.base import QuoteProvider


class _FakeProvider(QuoteProvider):
    name = "fake"

    def __init__(self, candles):
        self._candles = candles

    def fetch(self, symbol, granularity, since=None):
        return self._candles


def test_ensure_instrument_creates_once(conn):
    id1 = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    id2 = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    assert id1 == id2
    n = conn.execute("SELECT COUNT(*) c FROM instruments").fetchone()["c"]
    assert n == 1


def test_upsert_candles_inserts(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    candles = [
        Candle(ts="2026-01-01T00:00:00+00:00", close=9.0),
        Candle(ts="2026-01-02T00:00:00+00:00", close=9.5),
    ]
    n = quotes.upsert_candles(conn, iid, "daily", candles)
    assert n == 2
    rows = conn.execute("SELECT * FROM quotes WHERE instrument_id = ?", (iid,)).fetchall()
    assert len(rows) == 2


def test_upsert_candles_updates_on_conflict(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.upsert_candles(conn, iid, "daily", [Candle(ts="2026-01-01T00:00:00+00:00", close=9.0)])
    # dzisiejsza świeca dzienna jest prowizoryczna przed zamknięciem — drugi
    # fetch tego samego dnia z inną wartością MUSI nadpisać, nie zduplikować
    quotes.upsert_candles(conn, iid, "daily", [Candle(ts="2026-01-01T00:00:00+00:00", close=9.9)])
    rows = conn.execute("SELECT close FROM quotes WHERE instrument_id = ?", (iid,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["close"] == 9.9


def test_backfill_stores_candles_from_provider(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    fake = _FakeProvider([
        Candle(ts=f"2026-01-{d:02d}T00:00:00+00:00", close=float(d)) for d in range(1, 6)
    ])
    n = quotes.backfill(conn, iid, "NOKIA.HE", fake, years=1)
    assert n == 5


def test_daily_closes_chronological_order(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.upsert_candles(conn, iid, "daily", [
        Candle(ts="2026-01-03T00:00:00+00:00", close=3.0),
        Candle(ts="2026-01-01T00:00:00+00:00", close=1.0),
        Candle(ts="2026-01-02T00:00:00+00:00", close=2.0),
    ])
    assert quotes.daily_closes(conn, iid) == [1.0, 2.0, 3.0]


def test_latest_quote_returns_most_recent(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.upsert_candles(conn, iid, "daily", [
        Candle(ts="2026-01-01T00:00:00+00:00", close=1.0),
        Candle(ts="2026-01-02T00:00:00+00:00", close=2.0),
    ])
    latest = quotes.latest_quote(conn, iid, granularity="daily")
    assert latest["close"] == 2.0


def test_latest_quote_none_when_empty(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    assert quotes.latest_quote(conn, iid) is None
