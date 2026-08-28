"""Trasy /lots (odczyt) i /sales. Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client`/`seed` w conftest.py.

Etap 3 (docs/PLAN_0_28_0_ui_porzadki.md): `POST /lots`, `POST /lots/sell`
usunięte — loty/sprzedaże pochodzą wyłącznie z wyciągów Computershare.
Zasiew danych w tych testach przechodzi przez fixture `seed` (woła
bezpośrednio `tax/lots.py::add_lot`/`record_sale`, te same funkcje, które
wołały usunięte trasy) — asercje o skutkach (FIFO, kwoty, /sales, /lots)
zostają bez zmiany, bo silnik jest ten sam. Testy istniejące WYŁĄCZNIE po
to, by sprawdzić HTTP-zachowanie usuniętych tras (redirect, walidacja daty
przyszłej, błąd braku pokrycia przy POST) są usunięte —
`InsufficientLotsError` ma pokrycie silnikowe w test_tax_lots.py i
web-owe przez wciąż istniejącą `POST /imports/conflicts/<id>/confirm-sale`
(test_web_imports.py)."""
import pytest


# --- lots ---

@pytest.fixture
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_lots_page_empty_state(client):
    resp = client.get("/lots")
    html = resp.get_data(as_text=True)
    assert "Brak lotów" in html
    assert "kalkulator pomocniczy" in html


def test_lots_post_removed_returns_405(client):
    resp = client.post("/lots", data={"acquired_date": "2024-01-10"})
    assert resp.status_code == 405


def test_lots_sell_post_removed_returns_404(client):
    resp = client.post("/lots/sell", data={"sale_date": "2024-06-01"})
    assert resp.status_code == 404


def test_lots_page_has_no_manual_entry_forms(client):
    html = client.get("/lots").get_data(as_text=True)
    assert 'method="post" action="/lots"' not in html
    assert 'method="post" action="/lots/sell"' not in html


def test_lots_page_omits_policy_override_note_without_reported_value(
        client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 10, 8.0)

    html = client.get("/lots").get_data(as_text=True)

    assert "Zgłoszoną wartość" not in html


def test_lots_page_shows_policy_override_note_when_reported_value_set(
        client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 10, 8.0)
    client.post("/sales/1/report", data={
        "reported_revenue_pln": "999.0", "reported_cost_pln": "111.0"})

    html = client.get("/lots").get_data(as_text=True)

    assert "Zgłoszoną wartość" in html
    assert "2024-06-01" in html


def test_lots_page_shows_qty_remaining_after_sale(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 4, 8.0)

    html = client.get("/lots").get_data(as_text=True)
    assert "6.0000" in html  # qty_remaining po sprzedaży 4 z 10


def test_sales_page_uses_optional_real_proceeds_over_price_times_quantity(
        client, seed, _fake_nbp_rate):
    # krok 19: "Realne wpływy brutto" (`proceeds_eur`) zastępuje ilość×cenę.
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 4, 8.0, fee_eur=1.0, proceeds_eur=33.5)  # != 4*8.0=32.0

    html = client.get("/sales").get_data(as_text=True)
    # revenue_pln = (33.5 - 1.0) * 4.0 (kurs stub) = 130.00, NIE (4*8.0-1.0)*4.0 = 124.00
    assert "130.00" in html
    assert "124.00" not in html


def test_lots_page_shows_three_policies_comparison(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 10, 8.0)

    html = client.get("/lots").get_data(as_text=True)
    assert "Tylko własne" in html
    assert "Własne + dywidenda" in html
    assert "Wszystkie w wartości nabycia" in html


# --- sales (krok 16: pełne rozbicie zrealizowanych sprzedaży) ---

def test_sales_page_empty_state(client):
    resp = client.get("/sales")
    html = resp.get_data(as_text=True)
    assert "Brak zarejestrowanych sprzedaży" in html


def test_sales_page_shows_realized_sale_breakdown(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 4, 8.0)

    resp = client.get("/sales")
    html = resp.get_data(as_text=True)
    assert "2024-06-01" in html
    assert "2024-01-10" in html  # data nabycia lotu widoczna w rozwinięciu
    # kurs NBP zamockowany na "stub" (nie realna data) — bez numeru tabeli/linków
    # tu, ale wyprowadzenie kursu (event -> D-1 -> kurs) ma się i tak wyrenderować.
    assert "dzień roboczy poprzedzający" in html


def test_sales_page_filters_by_year(client, seed, _fake_nbp_rate):
    seed.lot("2023-01-10", 10, 5.0)
    seed.sale("2023-06-01", 4, 8.0)
    seed.sale("2024-06-01", 4, 8.0)

    resp_2023 = client.get("/sales?year=2023")
    resp_2024 = client.get("/sales?year=2024")
    assert "2023-06-01" in resp_2023.get_data(as_text=True)
    assert "2024-06-01" not in resp_2023.get_data(as_text=True)
    assert "2024-06-01" in resp_2024.get_data(as_text=True)


# ---- krok 20: zgłoszona wartość sprzedaży ----

def test_sales_report_post_sets_reported_values_and_affects_pit38(
        client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 10, 8.0)

    import re
    html = client.get("/sales").get_data(as_text=True)
    m = re.search(r'action="(/sales/\d+/report)"', html)
    assert m, "brak formularza zgłoszonej wartości w HTML"
    report_url = m.group(1)

    resp = client.post(report_url, data={
        "reported_revenue_pln": "999.0", "reported_cost_pln": "111.0",
        "reported_note": "zgodnie z arkuszem",
    })
    assert resp.status_code == 302
    assert "reported=1" in resp.headers["Location"]

    html_after = client.get("/sales").get_data(as_text=True)
    assert "999" in html_after
    assert "111" in html_after

    pit38 = client.get("/pit38?year=2024").get_data(as_text=True)
    assert "888.00" in pit38 or "888,00" in pit38  # income = 999-111 = 888


def test_sales_report_post_can_be_cleared(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 10, 8.0)

    import re
    html = client.get("/sales").get_data(as_text=True)
    report_url = re.search(r'action="(/sales/\d+/report)"', html).group(1)
    client.post(report_url, data={"reported_revenue_pln": "999.0", "reported_cost_pln": "111.0"})

    resp = client.post(report_url, data={"reported_revenue_pln": "", "reported_cost_pln": ""})
    assert resp.status_code == 302

    # engine: revenue=10*8*4=320, cost=10*5*4=200, income=120 (nie 888 z override)
    pit38 = client.get("/pit38?year=2024").get_data(as_text=True)
    assert "888.00" not in pit38 and "888,00" not in pit38


def test_sales_report_unknown_sale_id_redirects_safely(client):
    resp = client.post("/sales/999/report", data={"reported_revenue_pln": "1"})
    assert resp.status_code == 302


def test_sales_delete_restores_qty_remaining_and_redirects(client, seed, _fake_nbp_rate):
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 4, 8.0)

    resp_lots_before = client.get("/lots")
    assert "6.0000" in resp_lots_before.get_data(as_text=True)  # qty_remaining po sprzedaży

    import re
    html = client.get("/sales").get_data(as_text=True)
    sale_form_action = re.search(r'action="(/sales/\d+/delete)"', html)
    assert sale_form_action, "brak formularza cofnięcia sprzedaży w HTML"
    delete_url = sale_form_action.group(1)

    resp_del = client.post(delete_url)
    assert resp_del.status_code == 302
    assert "deleted=1" in resp_del.headers["Location"]

    resp_lots_after = client.get("/lots")
    assert "10.0000" in resp_lots_after.get_data(as_text=True)  # qty_remaining przywrócone

    resp_sales = client.get("/sales")
    assert "Brak zarejestrowanych sprzedaży" in resp_sales.get_data(as_text=True)


def test_sales_page_shows_year_totals(client, seed, _fake_nbp_rate):
    # Krok 17: rejestr sprzedaży dostaje pasek KPI z sumą za rok (nie tylko
    # per-sprzedaż). Kafelki podsumowania formatują PLN bez miejsc po przecinku
    # (konwencja strony, patrz dividends.html/dashboard.html) — cena dobrana tak,
    # żeby podatek per sprzedaż wyszedł całkowity: revenue 4*11.25*4=180,
    # koszt 4*5*4=80, dochód 100, podatek 19.00; dwie takie sprzedaże = 38 razem.
    seed.lot("2024-01-10", 20, 5.0)
    seed.sale("2024-03-01", 4, 11.25)
    seed.sale("2024-06-01", 4, 11.25)

    html = client.get("/sales?year=2024").get_data(as_text=True)
    assert "Podsumowanie 2024" in html
    assert '<span class="stat-value">38<span class="stat-unit">PLN</span></span>' in html


def test_sales_row_detail_rendered_server_side(client, seed, _fake_nbp_rate):
    # Krok 17: /sales przeszło z <details> na rejestr wiersz + wiersz-detal;
    # detal musi być wyrenderowany po stronie serwera (schowany przez `hidden`,
    # nie doładowany JS-em po kliknięciu).
    seed.lot("2024-01-10", 10, 5.0)
    seed.sale("2024-06-01", 4, 8.0)

    html = client.get("/sales").get_data(as_text=True)
    assert 'class="row-detail"' in html
    assert "hidden" in html
    assert "2024-01-10" in html  # data nabycia lotu widoczna w detalu, mimo hidden


def test_alloc_detail_renders_sale_fx_once(client, seed, _fake_nbp_rate):
    # Regresja: przed krokiem 17 wyprowadzenie kursu sprzedaży powtarzało się
    # w osobnym wierszu prozy PRZY KAŻDYM LOCIE (_alloc_detail.html/alloc-fx-row).
    # Sprzedaż konsumująca FIFO z dwóch lotów (3+1 z pierwszego, 3 z drugiego)
    # ma pokazać zdanie o kursie sprzedaży NAD TABELĄ ALOKACJI dokładnie raz,
    # niezależnie od liczby lotów.
    #
    # E10 (docs/PLAN_E10_dokumenty.md, Etap 1): od `breakdown.sale_traces()`
    # to samo zdanie dochodzi DRUGI raz jako `note` śladu „Przychód PLN” na
    # wierszu podsumowania (`sales.html`) — ten sam wzorzec, który już
    # istniał na `/wyplata` (`wyplata.revenue_pln` + `_alloc_detail.html`
    # razem na jednej stronie, nikt tego nie flagował). Dwa źródła, ZERO
    # skalowania z liczbą lotów — to jest właściwy niezmiennik do pilnowania.
    seed.lot("2024-01-05", 3, 5.0)
    seed.lot("2024-02-05", 3, 5.0)
    seed.sale("2024-06-01", 4, 8.0)

    html = client.get("/sales").get_data(as_text=True)
    assert html.count("sprzedaż: 2024-06-01") == 2


def test_alloc_detail_sale_fx_does_not_scale_with_lot_count(client, seed, _fake_nbp_rate):
    # Trzeci lot w FIFO nie dokłada trzeciego wystąpienia — obie lokalizacje
    # (fx-line + ślad E10) liczą kurs sprzedaży RAZ, nie per lot.
    seed.lot("2024-01-05", 2, 5.0)
    seed.lot("2024-02-05", 2, 5.0)
    seed.lot("2024-03-05", 2, 5.0)
    seed.sale("2024-06-01", 6, 8.0)

    html = client.get("/sales").get_data(as_text=True)
    assert html.count("sprzedaż: 2024-06-01") == 2
