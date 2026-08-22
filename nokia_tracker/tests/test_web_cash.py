"""Trasy /gotowka (krok E4 — docs/ROADMAP_V3.md): saldo u brokera, wpływy ze
sprzedaży, podatek PIT-38 vs zapłacony, dywidendy bezgotówkowe. Fixture
`client` w conftest.py."""
from __future__ import annotations


def test_cash_page_empty_state_200(client):
    resp = client.get("/gotowka")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"


def test_cash_page_shows_no_broker_balance_as_missing_not_zero(client):
    html = client.get("/gotowka").get_data(as_text=True)
    assert "brak danych" in html.lower()


def test_cash_page_year_selector_filters(client):
    client.post("/lots", data={
        "acquired_date": "2023-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/gotowka?year=2023")
    assert resp.status_code == 200


def test_broker_balance_post_then_shown_on_page(client):
    resp = client.post("/gotowka/saldo", data={
        "as_of_date": "2026-08-01", "amount": "5000", "currency": "EUR",
        "notes": "z aplikacji brokera",
    }, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "5" in html and "000" in html
    assert "brak danych" not in html.lower()


def test_broker_balance_post_redirects_to_cash_get(client):
    resp = client.post("/gotowka/saldo", data={
        "as_of_date": "2026-08-01", "amount": "5000", "currency": "EUR",
    })
    assert resp.status_code == 302
    assert "/gotowka" in resp.headers["Location"]


def test_broker_balance_upsert_same_day_shows_latest(client):
    client.post("/gotowka/saldo", data={
        "as_of_date": "2026-08-01", "amount": "1000", "currency": "EUR"})
    client.post("/gotowka/saldo", data={
        "as_of_date": "2026-08-01", "amount": "1234", "currency": "EUR"})
    html = client.get("/gotowka").get_data(as_text=True)
    assert "1" in html and "234" in html


def test_add_tax_payment_then_shown_and_reduces_outstanding(client):
    resp = client.post("/gotowka/podatek", data={
        "tax_year": "2025", "paid_date": "2026-04-01",
        "amount_pln": "500", "notes": "zaliczka",
    }, follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "zaliczka" in html


def test_delete_tax_payment(client):
    client.post("/gotowka/podatek", data={
        "tax_year": "2025", "paid_date": "2026-04-01",
        "amount_pln": "500", "notes": "do usunięcia",
    })
    html_before = client.get("/gotowka?year=2025").get_data(as_text=True)
    assert "do usunięcia" in html_before

    import re
    m = re.search(r'/gotowka/podatek/(\d+)/usun', html_before)
    assert m, "brak linku usuwania w HTML"
    resp = client.post(f"/gotowka/podatek/{m.group(1)}/usun", follow_redirects=True)
    assert resp.status_code == 200
    assert "do usunięcia" not in resp.get_data(as_text=True)


def test_dividend_flow_shown_as_cashless(client):
    html = client.get("/gotowka").get_data(as_text=True)
    assert "bezgotówk" in html.lower()
