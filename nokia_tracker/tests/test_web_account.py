"""Trasa Stanu konta: / (krok E5, docs/ROADMAP_V3.md). Zastąpiła dawny pulpit
(przeniósł się na /rynek, patrz `test_web_market.py`). Asercje portfelowe
(krok 21/23/26 — PLN, trzy kubełki, ograniczenie, overdue, forfeit)
przeniesione BEZ ZMIANY z dawnego `test_web_dashboard.py`, tylko pod inną
ścieżkę i inne nazwy testów — zero zmiany liczb, ta sama kompozycja
`views/account.py::account_view` co dawne `views/dashboard.py::dashboard_view`.
Fixture `client` w conftest.py."""
from nokia_tracker import db as dbm


def test_account_shows_quick_question_field_submitting_to_asystent(client):
    html = client.get("/").get_data(as_text=True)
    assert 'action="/asystent"' in html
    assert 'name="q"' in html


# --- krok 18: PLN na Stanie konta (kurs bieżący, nie NBP) ---

def _make_pln_account_app(tmp_path, filename="pln_account.db"):
    from nokia_tracker import db as dbm, quotes as quotesm
    from nokia_tracker.web import create_app

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    eurpln_id = quotesm.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx")
    quotesm.store_single_price(conn, eurpln_id, 4.3, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")
    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    quotesm.store_single_price(conn, primary_id, 4.0, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES "
        "('position_qty', '100'), ('avg_cost_eur', '3.5')")
    conn.commit()
    conn.close()
    return create_app(db_path)


def test_account_shows_pln_alongside_eur(tmp_path):
    app = _make_pln_account_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "zł" in html
        assert "≈" in html
        # market_value_eur = 100 * 4.0 = 400 EUR -> PLN = 400 * 4.3 = 1720
        # krok 23: hero „Wartość całkowita" formatuje z separatorem tysięcy (NBSP) — money()
        assert "1\xa0720" in html


def test_account_labels_current_rate_not_nbp(tmp_path):
    app = _make_pln_account_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "kurs bieżący, nie tabela NBP" in html


def test_account_omits_pln_when_no_fx_rate(client):
    html = client.get("/").get_data(as_text=True)
    assert "zł" not in html
    assert "None" not in html
    assert "kurs EUR/PLN niedostępny" in html


# --- krok 21: całkowite zestawienie portfela (uwolnione + z ograniczeniem + zablokowane) ---
# docs/PLAN_KROK_21_portfel_calkowity.md

def _make_full_portfolio_account_app(tmp_path, monkeypatch, filename="krok21_account.db"):
    from nokia_tracker import db as dbm, quotes as quotesm
    from nokia_tracker.tax import grants as grantsm, lots as taxlots
    from nokia_tracker.web import create_app

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, "stub"))

    db_path = str(tmp_path / filename)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    eurpln_id = quotesm.ensure_instrument(conn, "EURPLN=X", "EUR/PLN", "PLN", "fx")
    quotesm.store_single_price(conn, eurpln_id, 4.0, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")
    primary_id = quotesm.ensure_instrument(conn, "NOKIA.HE", "Nokia Oyj", "EUR", "primary")
    quotesm.store_single_price(conn, primary_id, 10.0, source="yahoo",
                               ts="2026-08-10T16:00:00+00:00")

    # Lot własny 100 szt. nabyty 2025-10-27 - restricted_own_summary dopasuje go do
    # grantu A (ta sama Allocation Date) i uzna za ograniczony do 2099-01-01.
    taxlots.add_lot(conn, "2025-10-27", "own", 100.0, 5.0, source="pdf_import")

    grant_a = grantsm.add_grant(conn, "espp", "2025-10-27", 50.0, "espp_grant:a")
    grantsm.add_vest(
        conn, grant_a, "2099-01-01", 50.0, "espp_vest:a", available_from="2099-01-01")
    grant_b = grantsm.add_grant(conn, "lti", "2020-01-01", None, "lti_grant:b")
    grantsm.add_vest(
        conn, grant_b, "2099-02-01", 20.0, "lti_vest:b", available_from="2099-02-01")
    grant_c = grantsm.add_grant(conn, "espp", "2019-01-01", 5.0, "espp_grant:c")
    grantsm.add_vest(
        conn, grant_c, "2020-01-01", 5.0, "espp_vest:c", available_from="2020-01-01")

    conn.close()
    return create_app(db_path)


def test_account_shows_three_portfolio_blocks_with_correct_totals(tmp_path, monkeypatch):
    # krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): trzy kubełki (Wolne/Z ograniczeniem/
    # Zablokowane) + hero „Wartość całkowita" zastąpiły dawne "W posiadaniu"/"Razem", liczby
    # przez money()/qty() (separator tysięcy NBSP, 2 miejsca dla ilości zamiast 4).
    app = _make_full_portfolio_account_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "Wolne" in html
        assert "Zablokowane" in html
        assert "Wartość całkowita" in html
        # zablokowane: upcoming_qty = 50 (grant A) + 20 (grant B) = 70
        assert "70,00" in html
        assert "700" in html  # upcoming_value_eur = 70 * 10
        assert "2\xa0800" in html  # upcoming_value_pln = 700 * 4
        # razem: position_qty (100) + upcoming_qty (70) = 170
        assert "170,00" in html
        assert "1\xa0700" in html  # 1000 (market_value) + 700 (upcoming)
        assert "6\xa0800" in html  # 4000 + 2800


def test_account_shows_restriction_line_when_own_lot_restricted(tmp_path, monkeypatch):
    app = _make_full_portfolio_account_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "Z ograniczeniem" in html  # krok 23: tytuł kubełka (Title Case, jak reszta trójki)
        assert "100,00" in html  # restricted_qty = cały lot własny
        assert "2099-01-01" in html  # free_until


def test_account_hides_restriction_line_when_nothing_restricted(client):
    html = client.get("/").get_data(as_text=True)
    assert "Z ograniczeniem" not in html  # krok 23: kubełek renderuje się tylko gdy restricted_qty > 0


def test_account_shows_overdue_warning_when_present(tmp_path, monkeypatch):
    app = _make_full_portfolio_account_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        assert "5.0000" in html  # overdue_qty (grant C)
        assert "wgraj najnowszy wyciąg" in html
        assert "/grants" in html
        assert "/imports" in html


def test_account_hides_overdue_warning_when_none_overdue(client):
    html = client.get("/").get_data(as_text=True)
    assert "wgraj najnowszy wyciąg" not in html


def test_account_empty_portfolio_blocks_render_without_error(client):
    # zero grantów, zero lotów - kubełek „Zablokowane"/hero „Wartość całkowita" (krok 23,
    # dawniej "Razem") muszą renderować się z zerami, nie wywalać szablonu (już pokryte przez
    # test_page_returns_200_with_no_store dla "/", ale sprawdzamy tu jawnie treść nowych bloków).
    html = client.get("/").get_data(as_text=True)
    assert "Zablokowane" in html
    assert "Wartość całkowita" in html


def test_account_restricted_note_shows_forfeit_amount(tmp_path, monkeypatch):
    app = _make_full_portfolio_account_app(tmp_path, monkeypatch, "krok26_account.db")
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
        # grant_a: match_rate = 50/100 = 0.5, lot 100 szt -> forfeit_qty=50,
        # forfeit_value_pln = 50 * 10 (price) * 4 (eurpln) = 2000
        assert "utratę 50,00 akcji dopasowania" in html
        assert "2\xa0000" in html
