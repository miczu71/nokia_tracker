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


def test_refresh_live_price_updates_close_preserves_ohlc(conn):
    from datetime import datetime, timezone
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    today_ts = datetime.now(timezone.utc).date().isoformat() + "T07:00:00+00:00"
    quotes.upsert_candles(conn, iid, "daily", [
        Candle(ts=today_ts, close=8.222, open=8.5, high=8.6, low=8.1, volume=12345.0)
    ], source="yahoo")

    quotes.refresh_live_price(conn, iid, 7.93, source="avanza")

    row = conn.execute(
        "SELECT * FROM quotes WHERE instrument_id = ? AND granularity = 'daily'", (iid,)
    ).fetchone()
    assert row["close"] == 7.93
    assert row["source"] == "avanza"
    # OHLC/volume zebrane przez Yahoo muszą przetrwać — to jest cały sens
    # częściowego UPDATE zamiast upsert_candles().
    assert row["open"] == 8.5
    assert row["high"] == 8.6
    assert row["low"] == 8.1
    assert row["volume"] == 12345.0
    # jeden wiersz na dzień — brak duplikatu
    n = conn.execute("SELECT COUNT(*) c FROM quotes WHERE instrument_id = ?", (iid,)).fetchone()["c"]
    assert n == 1


def test_refresh_live_price_inserts_when_no_today_row(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.refresh_live_price(conn, iid, 7.93, source="avanza")

    latest = quotes.latest_quote(conn, iid, granularity="daily")
    assert latest["close"] == 7.93
    assert latest["source"] == "avanza"
    assert latest["open"] is None  # brak OHLC — Yahoo jeszcze nie odświeżył


# ---- krok 16: closes_in_range / prune_intraday (wykres pulpitu z zakresem) ----

def test_closes_in_range_chronological_with_timestamps(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.upsert_candles(conn, iid, "daily", [
        Candle(ts="2026-01-03T00:00:00+00:00", close=3.0),
        Candle(ts="2026-01-01T00:00:00+00:00", close=1.0),
        Candle(ts="2026-01-02T00:00:00+00:00", close=2.0),
    ])
    points = quotes.closes_in_range(conn, iid, "daily")
    assert points == [
        ("2026-01-01T00:00:00+00:00", 1.0),
        ("2026-01-02T00:00:00+00:00", 2.0),
        ("2026-01-03T00:00:00+00:00", 3.0),
    ]


def test_closes_in_range_filters_by_since(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.upsert_candles(conn, iid, "daily", [
        Candle(ts="2026-01-01T00:00:00+00:00", close=1.0),
        Candle(ts="2026-01-05T00:00:00+00:00", close=5.0),
    ])
    points = quotes.closes_in_range(conn, iid, "daily", since="2026-01-03T00:00:00+00:00")
    assert points == [("2026-01-05T00:00:00+00:00", 5.0)]


def test_closes_in_range_separates_granularities(conn):
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    quotes.upsert_candles(conn, iid, "daily", [Candle(ts="2026-01-01T00:00:00+00:00", close=1.0)])
    quotes.upsert_candles(conn, iid, "intraday", [Candle(ts="2026-01-01T09:00:00+00:00", close=1.1)])
    assert quotes.closes_in_range(conn, iid, "daily") == [("2026-01-01T00:00:00+00:00", 1.0)]
    assert quotes.closes_in_range(conn, iid, "intraday") == [("2026-01-01T09:00:00+00:00", 1.1)]


def test_prune_intraday_removes_old_rows_only(conn):
    from datetime import datetime, timedelta, timezone
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    quotes.upsert_candles(conn, iid, "intraday", [
        Candle(ts=old_ts, close=1.0), Candle(ts=recent_ts, close=2.0)])

    removed = quotes.prune_intraday(conn, keep_days=60)

    assert removed == 1
    remaining = quotes.closes_in_range(conn, iid, "intraday")
    assert len(remaining) == 1
    assert remaining[0][1] == 2.0


def test_prune_intraday_never_touches_daily_candles(conn):
    from datetime import datetime, timedelta, timezone
    iid = quotes.ensure_instrument(conn, "NOKIA.HE", "Nokia", "EUR", "primary")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=900)).isoformat()
    quotes.upsert_candles(conn, iid, "daily", [Candle(ts=old_ts, close=1.0)])

    removed = quotes.prune_intraday(conn, keep_days=60)

    assert removed == 0
    assert len(quotes.closes_in_range(conn, iid, "daily")) == 1
