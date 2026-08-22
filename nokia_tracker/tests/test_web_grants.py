"""Trasy /grants (ESPP/LTI). Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
# --- grants (ESPP/LTI) ---

def _make_grants_app(tmp_path):
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import grants as grantsm
    from nokia_tracker.web import create_app

    db_path = str(tmp_path / "grants.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)

    # ESPP: grant 1:1 z transzą (jak zapisuje computershare_pdf.import_statement)
    espp_grant_id = grantsm.add_grant(
        conn, "espp", "2026-01-06", 12.0, "espp_grant:2026-01-06:12.0", match_pct=20.0)
    grantsm.add_vest(
        conn, espp_grant_id, "2026-07-06", 12.0, "espp_vest:2026-01-06:2026-07-06:12.0")

    # LTI: jeden grant, wiele transz (participation_description w natural_key)
    lti_grant_id = grantsm.add_grant(
        conn, "lti", "2025-07-07", None, "lti_grant:2025 RS AWARD 07-JUL-2025")
    grantsm.add_vest(
        conn, lti_grant_id, "2026-07-06", 634.0,
        "lti_vest:2025 RS AWARD 07-JUL-2025:2026-07-06:634.0")
    grantsm.add_vest(
        conn, lti_grant_id, "2027-07-06", 633.0,
        "lti_vest:2025 RS AWARD 07-JUL-2025:2027-07-06:633.0")
    conn.close()

    return create_app(db_path)


def test_grants_page_empty_state(client):
    resp = client.get("/grants")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Brak grantów ESPP" in html
    assert "Brak grantów LTI" in html


def test_grants_page_shows_espp_and_lti_grouped_with_tranches(tmp_path):
    app = _make_grants_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert "2026-01-06" in html
        assert "2025 RS AWARD 07-JUL-2025" in html
        assert "634" in html
        assert "633" in html
        assert "1267" in html  # suma transz LTI (634+633), bo grants.quantity=NULL


def test_grants_page_shows_overdue_badge_for_past_pending_tranches(tmp_path, monkeypatch):
    from datetime import datetime as _datetime
    # _make_grants_app tworzy transze datowane 2026-01-06/2026-07-06 - z ustalonym "dziś" w
    # przyszłości względem obu, żeby test nie zależał od realnego zegara systemowego.
    monkeypatch.setattr("nokia_tracker.tax.grants.datetime", type(
        "FixedDatetime", (), {"now": staticmethod(lambda tz=None: _datetime(2027, 1, 1))}))
    app = _make_grants_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert "zaległe — sprawdź wyciąg" in html


def test_grants_page_shows_valuation_for_open_and_realized_portions(tmp_path, monkeypatch):
    # Krok 16: transza dopasowana do lotu (reconcile_vesting), część sprzedana,
    # część nadal otwarta — strona ma pokazać obie wartości.
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import grants as grantsm
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker.web import create_app

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event", lambda conn, d: (4.0, d))

    db_path = str(tmp_path / "grants_valuation.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    grant_id = grantsm.add_grant(conn, "espp", "2022-10-26", 10.0, "espp_grant:x")
    grantsm.add_vest(conn, grant_id, "2023-08-01", 10.0, "espp_vest:x")
    taxlots.add_lot(conn, "2023-08-30", "matched", 10.0, 3.65, source="pdf_import")
    grantsm.reconcile_vesting(conn, today="2026-07-28")
    taxlots.record_sale(conn, "2026-01-15", 4.0, 9.0)
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert "2026-01-15" in html  # data zrealizowanej sprzedaży w rozwinięciu
        assert "niedopasowane" not in html  # jedyna transza tutaj jest reconciled=True


# --- krok 18: /grants — brak fantomowych wierszy dla niezrealizowanych transz ---

def test_grants_no_phantom_rows_for_unrealized_vests(tmp_path):
    # _make_grants_app tworzy 3 transze (1 ESPP + 2 LTI), żadna nie ma
    # zrealizowanej sprzedaży — przed fixem każda dostawała pusty
    # `<tr><td colspan="N">` mimo braku treści do pokazania.
    app = _make_grants_app(tmp_path)
    with app.test_client() as c:
        html = c.get("/grants").get_data(as_text=True)
        assert 'colspan="8"' not in html
        assert 'colspan="6"' not in html


