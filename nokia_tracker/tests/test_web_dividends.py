"""Trasy /dividends (+harmonogram) i /api/preview/dividend. Wydzielone z
`test_web.py` (E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py.

`_fake_nbp_rate` (baza) i `_fake_nbp_rate_dividends` (stawka + data specyficzna
dla tego pliku) współistnieją — dokładnie jak w oryginalnym `test_web.py`,
gdzie obie były zdefiniowane osobno i używane przez różne testy w tym samym
obszarze."""
import pytest


@pytest.fixture
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_dividends_post_rejects_future_pay_date(client):
    resp = client.post("/dividends", data={"pay_date": "2099-01-01", "gross_eur": "100.0"})
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "przyszłości" in resp2.get_data(as_text=True)


# --- dividends (krok 16: jedno źródło prawdy przez add_dividend, kwoty w PLN) ---

@pytest.fixture
def _fake_nbp_rate_dividends(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "2026-06-12"))


def test_dividends_post_computes_tax_and_stores_row(client, _fake_nbp_rate_dividends):
    resp = client.post("/dividends", data={
        "pay_date": "2026-06-15", "gross_eur": "100.0", "withholding_pct": "35.0",
    })
    assert resp.status_code == 302

    resp2 = client.get("/dividends")
    html = resp2.get_data(as_text=True)
    assert "2026-06-15" in html
    assert "400.00" in html  # gross_pln = 100 EUR * kurs 4.0
    # przykład BLUEPRINT skalowany kursem 4.0: 4 PLN dopłaty -> 16.00, 20 -> 80.00
    assert "16.00" in html
    assert "80.00" in html


def test_dividends_post_uses_default_withholding_when_blank(client, _fake_nbp_rate_dividends):
    resp = client.post("/dividends", data={"pay_date": "2026-06-15", "gross_eur": "100.0"})
    assert resp.status_code == 302
    resp2 = client.get("/dividends")
    # domyślne finnish_withholding_pct=35 -> ten sam wynik co jawne 35.0 powyżej
    assert "400.00" in resp2.get_data(as_text=True)


def test_dividends_post_with_drip_creates_lot_and_shows_reinvestment(
        client, _fake_nbp_rate_dividends):
    resp = client.post("/dividends", data={
        "pay_date": "2026-06-15", "gross_eur": "100.0", "withholding_pct": "35.0",
        "drip_purchase_date": "2026-06-20", "drip_price_eur": "3.50", "drip_shares": "18.5714",
    })
    assert resp.status_code == 302
    html = client.get("/dividends").get_data(as_text=True)
    assert "18.5714" in html
    assert "2026-06-20" in html
    assert "gotówka" not in html


def test_dividends_post_without_drip_shows_cash(client, _fake_nbp_rate_dividends):
    client.post("/dividends", data={"pay_date": "2026-06-15", "gross_eur": "100.0"})
    html = client.get("/dividends").get_data(as_text=True)
    assert "gotówka" in html


def test_dividends_page_shows_disclaimer(client):
    resp = client.get("/dividends")
    html = resp.get_data(as_text=True)
    assert "kalkulator pomocniczy" in html
    assert "nie doradztwo podatkowe" in html


# --- kalendarz i harmonogram dywidend (krok 30, 0.14.0) ---

def _seed_four_real_dividends(client, monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    for pay_date in ["2025-02-20", "2025-05-15", "2025-08-14", "2025-11-13"]:
        client.post("/dividends", data={
            "pay_date": pay_date, "gross_eur": "4.0", "quantity": "100.0",
            "withholding_pct": "35.0",
        })


def test_dividends_page_shows_calendar_and_schedule_cards(client, monkeypatch):
    _seed_four_real_dividends(client, monkeypatch)
    html = client.get("/dividends").get_data(as_text=True)
    assert "Kalendarz dywidend" in html
    assert "Ogłoszony harmonogram" in html
    assert "Założenia prognozy" in html


def test_dividends_empty_db_calendar_shows_reason_not_crash(client):
    resp = client.get("/dividends")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Za mało realnych wypłat" in html


def test_dividend_schedule_post_inserts_multiple_instalments(client):
    resp = client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
        "record_date_2": "2026-05-15", "per_share_2": "0.04",
    })
    assert resp.status_code == 302
    html = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" in html
    assert "2026-05-15" in html


def test_dividend_schedule_post_skips_blank_instalments(client):
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
        "record_date_2": "", "per_share_2": "",
    })
    html = client.get("/dividends").get_data(as_text=True)
    assert html.count("2026-02-18") >= 1


def test_dividend_schedule_post_rejects_missing_fiscal_year(client):
    resp = client.post("/dividends/harmonogram", data={
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
    })
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "rok obrotowy" in resp2.get_data(as_text=True).lower()


def test_dividend_schedule_post_rejects_all_blank_instalments(client):
    resp = client.post("/dividends/harmonogram", data={"fiscal_year": "2026"})
    assert resp.status_code == 302
    resp2 = client.get(resp.headers["Location"])
    assert "co najmniej jedną ratę" in resp2.get_data(as_text=True).lower()


def test_dividend_schedule_post_future_dates_are_accepted(client):
    """_is_future_date świadomie NIE stosuje się do harmonogramu — daty przyszłe
    są całym sensem tej tabeli."""
    resp = client.post("/dividends/harmonogram", data={
        "fiscal_year": "2030",
        "record_date_1": "2030-01-01", "per_share_1": "0.05",
    })
    assert resp.status_code == 302
    html = client.get("/dividends").get_data(as_text=True)
    assert "error" not in resp.headers.get("Location", "").lower()
    assert "2030-01-01" in html


def test_dividend_schedule_post_upserts_same_instalment(client):
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
    })
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-20", "per_share_1": "0.045",
        "confirmed_1": "on",
    })
    html = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" not in html
    assert "2026-02-20" in html
    assert html.count("potwierdzona") >= 1


def test_dividend_schedule_delete_removes_row(client):
    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2026",
        "record_date_1": "2026-02-18", "per_share_1": "0.04",
    })
    html = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" in html

    schedule_id = 1  # pierwszy wiersz w świeżej bazie testowej
    resp = client.post(f"/dividends/harmonogram/{schedule_id}/delete")
    assert resp.status_code == 302
    html2 = client.get("/dividends").get_data(as_text=True)
    assert "2026-02-18" not in html2


def test_dividends_lata_query_param_changes_horizon(client, monkeypatch):
    _seed_four_real_dividends(client, monkeypatch)
    html_1y = client.get("/dividends?lata=1").get_data(as_text=True)
    html_5y = client.get("/dividends?lata=5").get_data(as_text=True)
    assert "1 rok</strong>" in html_1y
    assert "5 lata</strong>" in html_5y


def test_dividends_lata_invalid_value_falls_back_to_default(client):
    resp = client.get("/dividends?lata=99")
    assert resp.status_code == 200
    assert "3 lata</strong>" in resp.get_data(as_text=True)


def test_dividends_totals_unchanged_by_schedule_rows(client, monkeypatch):
    """A4 (docs/PLAN_KROK_30_dywidendy.md): projekcja NIGDY nie wchodzi do `totals`
    liczonych z zaksięgowanej historii — dodanie harmonogramu nie może zmienić
    kafelków podsumowania na górze strony."""
    _seed_four_real_dividends(client, monkeypatch)
    before = client.get("/dividends").get_data(as_text=True)
    before_summary = before.split("Kalendarz dywidend")[0]

    client.post("/dividends/harmonogram", data={
        "fiscal_year": "2027",
        "record_date_1": "2027-02-18", "per_share_1": "0.10",
    })

    after = client.get("/dividends").get_data(as_text=True)
    after_summary = after.split("Kalendarz dywidend")[0]
    assert before_summary == after_summary


# --- krok 18: jedna matematyka dywidendowa (PLN na kursie NBP zamrożonym) ---

def test_dividends_totals_use_frozen_nbp_not_current_rate(client, _fake_nbp_rate):
    client.post("/dividends", data={
        "pay_date": "2024-06-15", "gross_eur": "100", "withholding_pct": "35"})
    client.post("/dividends", data={
        "pay_date": "2024-07-15", "gross_eur": "50", "withholding_pct": "35"})
    html = client.get("/dividends").get_data(as_text=True)
    # Kafelek "Brutto" musi być sumą wierszy tabeli (400.00 + 200.00), oba na
    # zamrożonym kursie NBP 4.0 z fixture'a — nie osobną kalkulacją EUR na
    # kursie bieżącym (dawny sensors.dividends_values w tej trasie).
    assert "400.00" in html
    assert "200.00" in html
    assert '<span class="stat-value">600<span class="stat-unit">PLN</span></span>' in html


def test_dividends_yield_on_cost_uses_lots_not_manual_settings(client, _fake_nbp_rate):
    import re
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "100", "price_eur": "3.5", "fee_eur": "0",
    })
    client.post("/dividends", data={
        "pay_date": "2024-06-15", "gross_eur": "100", "withholding_pct": "35"})
    html = client.get("/dividends").get_data(as_text=True)
    m = re.search(r'Yield on cost</span>\s*<span class="stat-value">([^<]+)', html)
    assert m is not None
    value = m.group(1).strip()
    assert value != "—"
    float(value)  # musi się dać sparsować jako liczba, nie pozostać myślnikiem


