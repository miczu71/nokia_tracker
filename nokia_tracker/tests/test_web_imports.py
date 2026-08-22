"""Trasy /imports (+konflikty). Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py."""
import pytest

from nokia_tracker import db as dbm
from nokia_tracker.web import create_app


# --- imports ---

def test_imports_page_empty_state(client):
    resp = client.get("/imports")
    html = resp.get_data(as_text=True)
    assert "Brak historii importów" in html
    assert "Brak nierozwiązanych konfliktów" in html


def test_imports_upload_calls_import_statement_and_redirects(client, monkeypatch):
    from io import BytesIO

    from nokia_tracker.importers import computershare_pdf

    calls = []
    monkeypatch.setattr(
        computershare_pdf, "import_statement",
        lambda conn, data, filename, cfg=None: (
            calls.append((filename, len(data))),
            {"import_id": 1, "rows_inserted": 5, "rows_unchanged": 2, "rows_conflict": 1})[1])

    resp = client.post("/imports/upload", data={
        "pdf_file": (BytesIO(b"%PDF-fake-content"), "wyciag.pdf"),
    }, content_type="multipart/form-data")

    assert resp.status_code == 302
    assert "report=5%2F2%2F1" in resp.headers["Location"] or "report=5/2/1" in resp.headers["Location"]
    assert len(calls) == 1
    assert calls[0][0] == "wyciag.pdf"


def test_imports_upload_reconciles_vesting_after_import(tmp_path, monkeypatch):
    # Krok 14: /imports/upload musi wywołać reconcile_vesting() zaraz po
    # import_statement(), żeby /grants pokazywało rozwiązaną transzę bez
    # dodatkowego requestu (docs/PLAN_KROK_14_vesting_reconcile.md).
    from io import BytesIO

    from nokia_tracker.importers import computershare_pdf
    from nokia_tracker.tax import grants as grantsm
    from nokia_tracker.tax import lots as taxlots

    db_path = str(tmp_path / "reconcile.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    grant_id = grantsm.add_grant(conn, "espp", "2022-10-26", 7.33, "espp_grant:x")
    vest_id = grantsm.add_vest(conn, grant_id, "2023-08-01", 7.33, "espp_vest:x")
    taxlots.add_lot(conn, "2023-08-30", "matched", 7.33, 3.65, source="pdf_import")
    conn.close()

    monkeypatch.setattr(
        computershare_pdf, "import_statement",
        lambda conn, data, filename, cfg=None: {
            "import_id": 1, "rows_inserted": 0, "rows_unchanged": 0, "rows_conflict": 0})

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.post("/imports/upload", data={
            "pdf_file": (BytesIO(b"%PDF-fake-content"), "wyciag.pdf"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 302

    conn2 = dbm.get_conn(db_path)
    vest = conn2.execute("SELECT * FROM vests WHERE id = ?", (vest_id,)).fetchone()
    assert vest["status"] == "vested"
    conn2.close()


def test_imports_upload_without_file_redirects_without_calling_import(client, monkeypatch):
    from nokia_tracker.importers import computershare_pdf

    calls = []
    monkeypatch.setattr(
        computershare_pdf, "import_statement",
        lambda *a, **kw: calls.append(1))

    resp = client.post("/imports/upload", data={}, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert len(calls) == 0


def _make_withhold_conflict_app(tmp_path, monkeypatch, quantity=784.0, sale_price_eur=5.31,
                                fees_eur=8.32, lot_quantity=1000.0):
    from nokia_tracker import db as dbm
    from nokia_tracker.tax import lots as taxlots
    from nokia_tracker.web import create_app
    import json as _json

    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))

    db_path = str(tmp_path / "confirm_sale.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    taxlots.add_lot(conn, "2022-01-01", "own", lot_quantity, 5.0)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('x.pdf', 'abc', '2025-01-01', '2026-01-01', '2026-01-01')")
    import_id = conn.execute("SELECT id FROM imports").fetchone()["id"]
    incoming = {
        "execution_date": "2025-10-27", "quantity": quantity, "sale_price_eur": sale_price_eur,
        "sale_proceeds_eur": quantity * sale_price_eur, "taxes_eur": 0.0, "fees_eur": fees_eur,
        "net_proceeds_eur": quantity * sale_price_eur - fees_eur,
    }
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'withhold_to_cover_sale', 'wtc:x', '{}', ?)",
        (import_id, _json.dumps(incoming)))
    conn.commit()
    conflict_id = conn.execute("SELECT id FROM import_conflicts").fetchone()["id"]
    conn.close()

    app = create_app(db_path)
    return app, conflict_id, db_path


def test_imports_page_shows_prefilled_sale_details_and_confirm_button(tmp_path, monkeypatch):
    app, conflict_id, _ = _make_withhold_conflict_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        html = c.get("/imports").get_data(as_text=True)
        assert "784" in html
        assert "5.31" in html
        assert "Zatwierdź jako sprzedaż" in html
        assert f"/imports/conflicts/{conflict_id}/confirm-sale" in html


def test_imports_confirm_sale_books_real_sale_and_resolves_conflict(tmp_path, monkeypatch):
    app, conflict_id, db_path = _make_withhold_conflict_app(tmp_path, monkeypatch)
    with app.test_client() as c:
        resp = c.post(f"/imports/conflicts/{conflict_id}/confirm-sale")
        assert resp.status_code == 302
        assert "sold=1" in resp.headers["Location"]

    from nokia_tracker import db as dbm
    conn = dbm.get_conn(db_path)
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    assert conflict["resolved"] == 1
    assert "sale_id" in conflict["resolution"]
    sale = conn.execute("SELECT * FROM sales").fetchone()
    assert sale is not None
    assert sale["quantity"] == pytest.approx(784.0)
    assert sale["price_eur"] == pytest.approx(5.31)
    conn.close()


def test_imports_confirm_sale_uses_real_sale_proceeds_not_price_times_quantity(
        tmp_path, monkeypatch):
    # krok 19: Sale Price w PDF jest zaokrąglona do 2 miejsc, więc quantity*price != realne
    # Sale Proceeds z wyciągu (znalezione na realnych danych: 784*5.31=4162.94 EUR,
    # a wyciąg podaje 4161.47 EUR) - confirm-sale musi zaksięgować to drugie.
    app, conflict_id, db_path = _make_withhold_conflict_app(tmp_path, monkeypatch)
    from nokia_tracker import db as dbm
    conn = dbm.get_conn(db_path)
    import json as _json
    row = conn.execute(
        "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    incoming = _json.loads(row["incoming_json"])
    incoming["sale_proceeds_eur"] = 4161.47  # różni się od 784*5.31=4162.94
    conn.execute(
        "UPDATE import_conflicts SET incoming_json = ? WHERE id = ?",
        (_json.dumps(incoming), conflict_id))
    conn.commit()
    conn.close()

    with app.test_client() as c:
        c.post(f"/imports/conflicts/{conflict_id}/confirm-sale")

    conn = dbm.get_conn(db_path)
    sale = conn.execute("SELECT * FROM sales").fetchone()
    # revenue_pln = (4161.47 - 8.32) * 4.0 (kurs stub), NIE (784*5.31 - 8.32) * 4.0
    assert sale["revenue_pln"] == pytest.approx((4161.47 - 8.32) * 4.0)
    conn.close()


def test_imports_confirm_sale_insufficient_lots_does_not_resolve_conflict(tmp_path, monkeypatch):
    # Lot dostępny tylko 10 szt, konflikt mówi o sprzedaży 784 - brak pokrycia.
    app, conflict_id, db_path = _make_withhold_conflict_app(tmp_path, monkeypatch, lot_quantity=10.0)
    with app.test_client() as c:
        resp = c.post(f"/imports/conflicts/{conflict_id}/confirm-sale")
        assert resp.status_code == 302
        assert "error=" in resp.headers["Location"]

    from nokia_tracker import db as dbm
    conn = dbm.get_conn(db_path)
    conflict = conn.execute(
        "SELECT * FROM import_conflicts WHERE id = ?", (conflict_id,)).fetchone()
    assert conflict["resolved"] == 0  # nie oznaczony rozwiązany przy błędzie
    assert conn.execute("SELECT COUNT(*) c FROM sales").fetchone()["c"] == 0
    conn.close()


def test_imports_confirm_sale_unknown_conflict_id_redirects_safely(client):
    resp = client.post("/imports/conflicts/999/confirm-sale")
    assert resp.status_code == 302


def test_imports_conflicts_queue_shows_unresolved_and_resolve_hides_it(tmp_path):
    from nokia_tracker import db as dbm
    from nokia_tracker.web import create_app

    db_path = str(tmp_path / "conflicts.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.execute(
        "INSERT INTO imports (filename, file_sha256, period_start, period_end, as_of_date) "
        "VALUES ('x.pdf', 'abc', '2026-01-01', '2026-07-26', '2026-07-26')")
    import_id = conn.execute("SELECT id FROM imports").fetchone()["id"]
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, existing_json, "
        "incoming_json) VALUES (?, 'lot', 'purchase:x', '{}', '{\"quantity\": 5}')",
        (import_id,))
    conn.commit()
    conflict_id = conn.execute("SELECT id FROM import_conflicts").fetchone()["id"]
    conn.close()

    app = create_app(db_path)
    with app.test_client() as c:
        resp = c.get("/imports")
        assert "purchase:x" in resp.get_data(as_text=True)

        resp2 = c.post(f"/imports/conflicts/{conflict_id}/resolve",
                       data={"resolution": "wpisano ręcznie"})
        assert resp2.status_code == 302

        resp3 = c.get("/imports")
        assert "purchase:x" not in resp3.get_data(as_text=True)
        assert "Brak nierozwiązanych konfliktów" in resp3.get_data(as_text=True)


