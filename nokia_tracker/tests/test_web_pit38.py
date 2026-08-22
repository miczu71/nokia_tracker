"""Trasy /pit38 (+eksporty CSV/XLSX) i /pit38/kreator. Wydzielone z
`test_web.py` (E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
import re
from datetime import datetime

import pytest

from nokia_tracker import db as dbm
from nokia_tracker.web import create_app


# --- PIT-38 (krok 15) ---

@pytest.fixture
def _fake_nbp_rate_pit38(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def test_pit38_page_empty_state_shows_disclaimer(client):
    resp = client.get("/pit38")
    html = resp.get_data(as_text=True)
    assert "kalkulator pomocniczy" in html


def test_pit38_page_shows_three_policies_and_section_g(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp = client.get("/pit38?year=2024")
    html = resp.get_data(as_text=True)
    assert "Tylko własne" in html
    assert "Własne + dywidenda" in html
    assert "Wszystkie w wartości nabycia" in html
    assert "Sekcja G" in html
    assert "PIT/ZG" in html


def test_pit38_page_year_selector_filters_sale_trace(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2023-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2023-06-01", "sale_quantity": "5",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp_2023 = client.get("/pit38?year=2023")
    resp_2024 = client.get("/pit38?year=2024")
    assert "2023-01-10" in resp_2023.get_data(as_text=True)
    assert "2023-01-10" not in resp_2024.get_data(as_text=True)


def test_pit38_year_selector_lists_years_with_data(client, _fake_nbp_rate_pit38):
    # Krok 16 (§8.3): selektor to lista lat z rzeczywistymi zdarzeniami, nie
    # gołe pole liczbowe — 2023 (sprzedaż) i bieżący rok muszą się pojawić.
    client.post("/lots", data={
        "acquired_date": "2023-01-10", "lot_type": "own",
        "quantity": "5", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2023-06-01", "sale_quantity": "5",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    html = client.get("/pit38").get_data(as_text=True)
    assert '<option value="2023"' in html
    assert f'<option value="{datetime.now().year}"' in html


def test_pit38_shows_total_due(client, _fake_nbp_rate_pit38):
    # Krok 17: karta "Do wpisania w deklarację" pokazuje RAZEM DO ZAPŁATY =
    # podatek poz. C (wg aktywnej polityki) + dopłata sekcji G. Bez dywidend
    # w tym roku sekcja G = 0, więc razem = sam podatek poz. C: revenue
    # 10*8*4=320 - koszt 10*5*4=200 = 120 dochodu * 19% = 22.80.
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    client.post("/lots/sell", data={
        "sale_date": "2024-06-01", "sale_quantity": "10",
        "sale_price_eur": "8.0", "sale_fee_eur": "0",
    })
    resp = client.get("/pit38?year=2024")
    html = resp.get_data(as_text=True)
    assert "RAZEM DO ZAPŁATY" in html
    assert "22.80" in html or "22,80" in html


def test_pit38_page_whatif_query_params_render_result(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/pit38?whatif_qty=5&whatif_price=8.0")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # revenue (5*8*4=160) - cost (5*5*4=100) = 60 dochodu * 19% ~ 11.40
    assert "11.40" in html or "11,40" in html


def test_pit38_page_whatif_insufficient_lots_shows_error_not_500(client, _fake_nbp_rate_pit38):
    resp = client.get("/pit38?whatif_qty=999&whatif_price=8.0")
    assert resp.status_code == 200
    assert "Brak pokrycia" in resp.get_data(as_text=True)


def test_pit38_print_mode_marks_page_for_print(client):
    resp = client.get("/pit38?print=1")
    html = resp.get_data(as_text=True)
    assert "print-mode" in html


def test_pit38_export_csv_returns_csv_attachment(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/pit38/export.csv?year=2024")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    assert "pit38_2024.csv" in resp.headers["Content-Disposition"]


def test_pit38_export_xlsx_returns_xlsx_attachment(client, _fake_nbp_rate_pit38):
    client.post("/lots", data={
        "acquired_date": "2024-01-10", "lot_type": "own",
        "quantity": "10", "price_eur": "5.0", "fee_eur": "0",
    })
    resp = client.get("/pit38/export.xlsx?year=2024")
    assert resp.status_code == 200
    assert resp.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "pit38_2024.xlsx" in resp.headers["Content-Disposition"]
    # niepusty realny plik XLSX (magic bytes ZIP - openpyxl zapisuje jako zip)
    assert resp.data[:2] == b"PK"


# --- /pit38/kreator (krok 27, docs/PLAN_KROK_27_straty_kreator.md) ---

def _make_wizard_app(tmp_path, monkeypatch, filename="krok27_wizard.db"):
    """2023: strata (kupno 10 szt. po 10 EUR, sprzedaż po 5 EUR -> strata 200 PLN
    przy stałym kursie 4.0). 2024: dochód (kupno 10 szt. po 5 EUR, sprzedaż po
    8 EUR -> dochód 120 PLN, podatek 22.80 PLN bez odliczenia straty). Wywołuje
    `losses.rebuild()` od razu, żeby `tax_loss_carryforward` było zasilone
    niezależnie od tego, czy test w ogóle odwiedza /pit38/kreator (który sam
    robi rebuild) — patrz `tax/losses.py::rebuild`."""
    from nokia_tracker.tax import losses as lossesm
    from nokia_tracker.tax import lots as taxlots

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    taxlots.add_lot(conn, "2023-01-10", "own", 10, 10.0)
    taxlots.record_sale(conn, "2023-06-01", 10, 5.0)
    taxlots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    taxlots.record_sale(conn, "2024-06-01", 10, 8.0)
    conn.commit()

    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    lossesm.rebuild(conn, cfg)
    conn.close()
    return create_app(db_path), db_path


def test_pit38_wizard_checklist_shows_missing_import(client):
    html = client.get("/pit38/kreator").get_data(as_text=True)
    assert "Wgraj wyciąg za ten rok" in html
    assert "Brak zaimportowanego wyciągu z datą &#39;as of&#39; w tym roku." in html \
        or "Brak zaimportowanego wyciągu z datą 'as of' w tym roku." in html


def test_pit38_wizard_page_lists_deduction_form_for_available_loss(tmp_path, monkeypatch):
    app, _db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/pit38/kreator?year=2024").get_data(as_text=True)
        assert "Strata z 2023" in html
        assert "Zapisz odliczenie" in html


def test_pit38_wizard_deduct_valid_amount_reduces_total_due(tmp_path, monkeypatch):
    app, _db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        before = c.get("/pit38?year=2024").get_data(as_text=True)
        assert "22.80" in before or "22,80" in before

        wizard_html = c.get("/pit38/kreator?year=2024").get_data(as_text=True)
        loss_id = re.search(r'name="loss_id" value="(\d+)"', wizard_html).group(1)

        resp = c.post(
            "/pit38/kreator/odlicz",
            data={"year": "2024", "loss_id": loss_id, "amount_pln": "50"},
            follow_redirects=False)
        assert resp.status_code == 302
        assert "deduct_error" not in resp.headers["Location"]

        # dochód po odliczeniu: 120 - 50 = 70 * 19% = 13.30
        after = c.get("/pit38?year=2024").get_data(as_text=True)
        assert "13.30" in after or "13,30" in after


def test_pit38_wizard_deduct_over_limit_redirects_with_error_and_no_write(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        wizard_html = c.get("/pit38/kreator?year=2024").get_data(as_text=True)
        loss_id = re.search(r'name="loss_id" value="(\d+)"', wizard_html).group(1)

        conn = dbm.get_conn(db_path)
        before_count = conn.execute("SELECT COUNT(*) FROM tax_loss_deductions").fetchone()[0]
        conn.close()

        resp = c.post(
            "/pit38/kreator/odlicz",
            data={"year": "2024", "loss_id": loss_id, "amount_pln": "99999"},
            follow_redirects=False)
        assert resp.status_code == 302
        assert "deduct_error" in resp.headers["Location"]

        conn = dbm.get_conn(db_path)
        after_count = conn.execute("SELECT COUNT(*) FROM tax_loss_deductions").fetchone()[0]
        conn.close()
        assert after_count == before_count == 0


def test_pit38_wizard_close_blocked_by_unresolved_balance_conflict(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    conn = dbm.get_conn(db_path)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','y','2024-12-31')")
    import_id = conn.execute("SELECT id FROM imports").fetchone()[0]
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json, resolved) VALUES (?, 'balance', 'k', '{}', '{}', 0)", (import_id,))
    conn.commit()
    conn.close()

    with app.test_client() as c:
        resp = c.post("/pit38/kreator/zamknij", data={"year": "2024"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "close_error=1" in resp.headers["Location"]

    conn = dbm.get_conn(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM tax_year_closed WHERE year=2024").fetchone()[0] == 0
    conn.close()


def test_pit38_wizard_close_succeeds_and_writes_snapshot_when_steps_done(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    conn = dbm.get_conn(db_path)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','y','2024-12-31')")
    conn.commit()
    conn.close()

    with app.test_client() as c:
        resp = c.post("/pit38/kreator/zamknij", data={"year": "2024"}, follow_redirects=False)
        assert resp.status_code == 302
        assert "closed=1" in resp.headers["Location"]

    conn = dbm.get_conn(db_path)
    row = conn.execute("SELECT * FROM tax_year_closed WHERE year=2024").fetchone()
    conn.close()
    assert row is not None
    assert row["total_due_pln_snapshot"] == pytest.approx(22.80)


def test_pit38_wizard_reopen_clears_tax_year_closed(tmp_path, monkeypatch):
    app, db_path = _make_wizard_app(tmp_path, monkeypatch)
    conn = dbm.get_conn(db_path)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, as_of_date) VALUES ('x','y','2024-12-31')")
    conn.commit()
    conn.close()

    with app.test_client() as c:
        c.post("/pit38/kreator/zamknij", data={"year": "2024"})
        resp = c.post("/pit38/kreator/odblokuj", data={"year": "2024"}, follow_redirects=False)
        assert resp.status_code == 302

    conn = dbm.get_conn(db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM tax_year_closed WHERE year=2024").fetchone()[0] == 0
    conn.close()


def test_pit38_page_shows_available_loss_card_and_wizard_link(tmp_path, monkeypatch):
    app, _db_path = _make_wizard_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/pit38?year=2024").get_data(as_text=True)
        assert "Straty z lat ubiegłych" in html
        assert "200.00" in html  # total_remaining_pln
        assert 'href="/pit38/kreator?year=2024"' in html


def test_pit38_page_shows_no_loss_message_when_none_available(client):
    html = client.get("/pit38").get_data(as_text=True)
    assert "Brak dostępnych strat" in html
    assert "/pit38/kreator" not in html


# --- krok 18: /pit38 Sekcja G schowana, gdy brak dywidend ---

def test_pit38_section_g_hidden_when_no_dividends(client):
    html = client.get("/pit38").get_data(as_text=True)
    assert "Brak dywidend w" in html


