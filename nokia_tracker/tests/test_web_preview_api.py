"""Podglądy JSON na żywo /api/preview/lot, /sale, /dividend (krok 18) —
usunięte w Etapie 3 (docs/PLAN_0_28_0_ui_porzadki.md) razem z formularzami
ręcznego wpisu, które podglądały: żaden szablon już ich nie woła. Zostaje
regresja na to, że naprawdę zniknęły. `/api/preview/sale_timing` (Plan) i
`/api/preview/espp`/`/api/preview/wyplata` NIE są dotknięte — patrz
odpowiednio test_web_plan.py."""


def test_preview_lot_removed_returns_404(client):
    resp = client.get(
        "/api/preview/lot?acquired_date=2024-01-10&quantity=10&price_eur=5&fee_eur=0")
    assert resp.status_code == 404


def test_preview_sale_removed_returns_404(client):
    resp = client.get(
        "/api/preview/sale?sale_date=2024-06-01&quantity=5&price_eur=8&fee_eur=0")
    assert resp.status_code == 404


def test_preview_dividend_removed_returns_404(client):
    resp = client.get(
        "/api/preview/dividend?pay_date=2024-06-15&gross_eur=100&withholding_pct=35")
    assert resp.status_code == 404
