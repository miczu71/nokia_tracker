"""Podglądy JSON na żywo: /api/preview/lot, /sale, /dividend (krok 18).
Wydzielone z `test_web.py` (E3 — docs/ROADMAP_V3.md); fixture `client` w
conftest.py."""
import pytest


@pytest.fixture
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


# --- krok 18: podgląd na żywo (/api/preview/lot, /sale, /dividend) ---

def test_preview_lot_returns_nbp_rate_and_cost_pln(client, _fake_nbp_rate):
    resp = client.get(
        "/api/preview/lot?acquired_date=2024-01-10&quantity=10&price_eur=5&fee_eur=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["nbp_rate"] == 4.0
    pln_line = next(l for l in data["lines"] if l["label"] == "Koszt" and l["unit"] == "PLN")
    assert pln_line["value"] == 200.0  # 10 * 5 * 4.0


def test_preview_sale_matches_recorded_sale(client, _fake_nbp_rate):
    # Regresja: podgląd (przed zapisem) i realna zapisana sprzedaż (po zapisie)
    # muszą dać DOKŁADNIE tę samą kwotę podatku — silnik (`simulate_sale`) jest
    # ten sam co pod `/lots/sell`.
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "20", "price_eur": "5.0", "fee_eur": "0",
    })
    preview = client.get(
        "/api/preview/sale?sale_date=2024-06-01&quantity=4&price_eur=11.25&fee_eur=0"
    ).get_json()
    assert preview["ok"] is True
    preview_tax = next(l["value"] for l in preview["lines"] if l["label"] == "Podatek")
    preview_net = next(l["value"] for l in preview["lines"] if l["label"] == "Na rękę")

    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "4",
        "sale_price_eur": "11.25", "sale_fee_eur": "0",
    })
    html = client.get("/sales").get_data(as_text=True)
    assert f"{preview_tax:.2f}" in html
    assert f"{preview_net:.2f}" in html


def test_preview_sale_insufficient_lots_returns_ok_false_not_500(client):
    resp = client.get(
        "/api/preview/sale?sale_date=2024-06-01&quantity=5&price_eur=8&fee_eur=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "error" in data


def test_preview_dividend_matches_stored_row(client, _fake_nbp_rate):
    preview = client.get(
        "/api/preview/dividend?pay_date=2024-06-15&gross_eur=100&withholding_pct=35"
    ).get_json()
    assert preview["ok"] is True
    preview_due = next(l["value"] for l in preview["lines"] if l["label"] == "Dopłata w PL")

    client.post("/dividends", data={
        "pay_date": "2024-06-15", "gross_eur": "100", "withholding_pct": "35"})
    html = client.get("/dividends").get_data(as_text=True)
    assert f"{preview_due:.2f}" in html


def test_preview_rejects_future_date(client):
    resp = client.get(
        "/api/preview/lot?acquired_date=2099-01-01&quantity=1&price_eur=1&fee_eur=0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "przyszłości" in data["error"]


