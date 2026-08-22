"""Trasa /wyniki — XIRR/TWR/atrybucja/benchmark (krok 25,
docs/PLAN_KROK_25_wyniki.md). Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
from nokia_tracker import db as dbm
from nokia_tracker.web import create_app


# --- krok 25 (0.9.0): /wyniki - XIRR/TWR/atrybucja/benchmark ---

def test_wyniki_page_empty_state(client):
    html = client.get("/wyniki").get_data(as_text=True)
    assert "Wyniki" in html
    assert "Brak jeszcze danych" in html


def test_wyniki_page_shows_xirr_twr_and_attribution(tmp_path, monkeypatch):
    from nokia_tracker import fx
    from nokia_tracker import quotes as quotesm
    from nokia_tracker.models import Candle
    from nokia_tracker.tax import lots as taxlots

    db_path = str(tmp_path / "wyniki.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda c, event_date: (4.0, "stub"))
    taxlots.add_lot(conn, "2023-01-01", "own", 10, 5.0)
    conn.execute(
        "INSERT INTO portfolio_history (date, position_qty, market_value_eur, "
        "market_value_pln) VALUES ('2024-01-01', 10.0, 60.0, 270.0), "
        "('2024-06-01', 10.0, 80.0, 360.0)")

    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    eurpln_id = quotesm.ensure_instrument(conn, fx.EURPLN_SYMBOL, "EUR/PLN", "PLN", "fx")
    quotesm.upsert_candles(conn, primary_id, "daily",
                           [Candle(ts="2024-06-01T00:00:00+00:00", close=8.0)], source="yahoo")
    quotesm.upsert_candles(conn, eurpln_id, "daily",
                           [Candle(ts="2024-06-01T00:00:00+00:00", close=4.5)], source="yahoo")
    conn.commit()
    conn.close()

    client = create_app(db_path).test_client()
    resp = client.get("/wyniki")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "XIRR" in html
    assert "TWR" in html
    assert "Atrybucja" in html or "atrybucj" in html.lower()


def test_wyniki_page_yearly_table_from_portfolio_history(tmp_path):
    db_path = str(tmp_path / "wyniki2.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.execute(
        "INSERT INTO portfolio_history (date, position_qty, market_value_eur, "
        "market_value_pln) VALUES "
        "('2023-06-01', 5.0, 50.0, 220.0), ('2023-12-30', 5.0, 55.0, 245.0), "
        "('2024-01-02', 5.0, 55.0, 245.0), ('2024-06-01', 5.0, 60.0, 270.0)")
    conn.commit()
    conn.close()

    client = create_app(db_path).test_client()
    html = client.get("/wyniki").get_data(as_text=True)
    assert "2023" in html
    assert "2024" in html


