"""Jednorazowa naprawa danych z audytu E1 (docs/ROADMAP_V3.md) — brakujący lot
dopasowania ESPP (grant id=6, 24.42 szt., vest 2025-08-01) i przeliczenie
alokacji jedynej realnej sprzedaży (#1, 2025-10-27, 784 szt.), która bez tego
lotu sięgnęła 8.48 szt. za dużo do droższego, PÓŹNIEJSZEGO lotu.

Fixtura odtwarza tylko RELEWANTNY fragment realnego łańcucha FIFO (nie
wszystkie 58 lotów) — przed-paczka jako jeden zagregowany lot (matematyka
_plan_fifo sumuje qty_remaining niezależnie od liczby lotów), paczka z
2025-08-28 (loty odpowiadające prawdziwym id 29/32) i lot z dnia sprzedaży
(odpowiednik prawdziwego id 14) — z DOKŁADNYMI liczbami z produkcji, żeby
test faktycznie zweryfikował tę konkretną naprawę, nie generyczny mechanizm."""
from __future__ import annotations

import pytest

from nokia_tracker import data_fixes
from nokia_tracker.tax import grants, lots as taxlots


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, event_date))
    return None


def _setup_pre_fix_state(conn):
    """Odtwarza stan produkcyjny SPRZED naprawy: sprzedaż #1 już zaksięgowana
    (bez brakującego lotu), grant/vest #8 wciąż 'pending'."""
    before_id = taxlots.add_lot(
        conn, "2025-01-01", "own", 673.640618, 1.0, source="pdf_import")
    batch1_id = taxlots.add_lot(
        conn, "2025-08-28", "matched", 0.48, 3.71, source="pdf_import",
        natural_key="vested_matching:2025-08-28:3.71:0.48")
    batch2_id = taxlots.add_lot(
        conn, "2025-08-28", "matched", 101.396662, 3.71, source="pdf_import",
        natural_key="vested_release:2025-08-28:3.71:101.396662")
    late_id = taxlots.add_lot(
        conn, "2025-10-27", "own", 19.49484, 5.41, source="pdf_import")

    cur = conn.execute(
        "INSERT INTO sales (sale_date, quantity, price_eur, fee_eur, nbp_rate, "
        "nbp_rate_date, revenue_pln, reported_revenue_pln, reported_cost_pln) "
        "VALUES ('2025-10-27', 784.0, 5.31, 8.32, 4.2353, '2025-10-24', "
        "17596.485615999998, 17631.72, 7500.66)")
    sale_id = cur.lastrowid
    assert sale_id == 1

    pre_fix_allocations = [
        (before_id, 673.640618),
        (batch1_id, 0.48),
        (batch2_id, 101.396662),
        (late_id, 8.482719999999816),
    ]
    for lot_id, qty in pre_fix_allocations:
        per_share = conn.execute(
            "SELECT cost_pln / quantity FROM lots WHERE id = ?", (lot_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO sale_allocations (sale_id, lot_id, quantity, cost_pln, revenue_pln) "
            "VALUES (?, ?, ?, ?, ?)",
            (sale_id, lot_id, qty, per_share * qty, qty * 5.31 * 4.2353))
        conn.execute(
            "UPDATE lots SET qty_remaining = qty_remaining - ? WHERE id = ?", (qty, lot_id))
    conn.commit()

    grant_id = grants.add_grant(conn, "espp", "2024-10-21", 24.42, "espp_grant:2024-10-21:24.42")
    vest_id = grants.add_vest(
        conn, grant_id, "2025-08-01", 24.42, "espp_vest:2024-10-21:2025-08-01:24.42",
        status="pending")
    conn.commit()
    return {
        "before_id": before_id, "batch1_id": batch1_id, "batch2_id": batch2_id,
        "late_id": late_id, "sale_id": sale_id, "grant_id": grant_id, "vest_id": vest_id,
    }


def test_inserts_missing_lot_with_expected_shape(conn):
    _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    lot = conn.execute(
        "SELECT * FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone()
    assert lot is not None
    assert lot["lot_type"] == "matched"
    assert lot["acquired_date"] == "2025-08-28"
    assert lot["quantity"] == pytest.approx(24.42)
    assert lot["price_eur"] == pytest.approx(3.71)


def test_links_vest_to_new_lot(conn):
    ids = _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "vested"
    lot = conn.execute(
        "SELECT * FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone()
    assert vest["lot_id"] == lot["id"]


def test_reallocates_sale_without_touching_late_lot(conn):
    """Kluczowe zachowanie: z brakującym lotem w łańcuchu FIFO sprzedaż #1
    NIE sięga już do lotu z dnia sprzedaży (5.41 EUR) — zatrzymuje się
    wcześniej, na tańszej (3.71 EUR) partii z 2025-08-28."""
    ids = _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    late_alloc = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = 1 AND lot_id = ?",
        (ids["late_id"],)).fetchone()
    assert late_alloc is None

    late_lot = conn.execute("SELECT * FROM lots WHERE id = ?", (ids["late_id"],)).fetchone()
    assert late_lot["qty_remaining"] == pytest.approx(19.49484)


def test_new_lot_partially_consumed_by_sale(conn):
    """8.48272 szt. brakowało do pokrycia 784 — dokładnie tyle bierze z nowego
    lotu (24.42 dostępnych), reszta (15.93728) zostaje w portfelu."""
    _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    lot = conn.execute(
        "SELECT * FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone()
    alloc = conn.execute(
        "SELECT * FROM sale_allocations WHERE sale_id = 1 AND lot_id = ?", (lot["id"],)
    ).fetchone()
    assert alloc["quantity"] == pytest.approx(8.482719999999816)
    assert lot["qty_remaining"] == pytest.approx(24.42 - 8.482719999999816)


def test_sale_allocation_sum_still_matches_sale_quantity(conn):
    ids = _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    total = conn.execute(
        "SELECT SUM(quantity) FROM sale_allocations WHERE sale_id = ?", (ids["sale_id"],)
    ).fetchone()[0]
    assert total == pytest.approx(784.0)


def test_reported_pit38_override_untouched(conn):
    """Nadpisanie z kroku 20 (reported_cost_pln/reported_revenue_pln) chroni
    już złożone zeznanie — ta naprawa nie może go dotknąć."""
    ids = _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (ids["sale_id"],)).fetchone()
    assert sale["reported_cost_pln"] == pytest.approx(7500.66)
    assert sale["reported_revenue_pln"] == pytest.approx(17631.72)


def test_idempotent_on_second_call(conn):
    _setup_pre_fix_state(conn)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    count = conn.execute(
        "SELECT COUNT(*) FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone()[0]
    assert count == 1
    total = conn.execute(
        "SELECT SUM(quantity) FROM sale_allocations WHERE sale_id = 1").fetchone()[0]
    assert total == pytest.approx(784.0)


def test_integrity_clean_after_fix(conn):
    from nokia_tracker import integrity

    _setup_pre_fix_state(conn)
    pre = integrity.check_all(conn, today="2026-08-22")
    assert any(f.check == "stale_pending_vest" for f in pre)

    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    post = integrity.check_all(conn, today="2026-08-22")
    assert post == []


# --- 2026-08-24: cofnięcie naprawy powyżej (re-import 6 wyciągów odsłonił, że jej
# założenie było błędne — te 24.42 szt. BYŁY już policzone w `batch2_id`
# ('vested_release:2025-08-28:3.71:101.396662'), Computershare łączy w jeden wiersz
# Withhold-to-Cover wszystkie transze dopasowania ESPP odblokowane tego samego dnia.
# Testy niżej łańcuchują DOKŁADNIE realną sekwencję zdarzeń: stan sprzed naprawy →
# błędna naprawa (jak na produkcji 2026-08-22) → cofnięcie (2026-08-24) — zamiast
# osobnej fixtury, żeby zweryfikować, że cofnięcie faktycznie odwraca TĘ KONKRETNĄ
# naprawę, nie generyczny mechanizm. ---


def test_revert_removes_the_lot_fix_added(conn):
    _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    lot = conn.execute(
        "SELECT * FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone()
    assert lot is None


def test_revert_puts_vest_back_to_pending(conn):
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "pending"
    assert vest["lot_id"] is None


def test_revert_reproduces_exact_pre_fix_allocation(conn):
    """FIFO bez fantomu, nad TYMI SAMYMI lotami, musi odtworzyć DOKŁADNIE
    alokację sprzed naprawy — deterministyczność FIFO nad niezmienionym
    zestawem realnych lotów."""
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    allocs = {a["lot_id"]: a["quantity"] for a in conn.execute(
        "SELECT lot_id, quantity FROM sale_allocations WHERE sale_id = 1").fetchall()}
    assert allocs == {
        ids["before_id"]: pytest.approx(673.640618),
        ids["batch1_id"]: pytest.approx(0.48),
        ids["batch2_id"]: pytest.approx(101.396662),
        ids["late_id"]: pytest.approx(8.482719999999816),
    }
    assert sum(allocs.values()) == pytest.approx(784.0)


def test_revert_leaves_reported_pit38_override_untouched(conn):
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (ids["sale_id"],)).fetchone()
    assert sale["reported_cost_pln"] == pytest.approx(7500.66)
    assert sale["reported_revenue_pln"] == pytest.approx(17631.72)


def test_revert_is_idempotent(conn):
    _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)
    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    # Druga naprawa nie ma już czego robić — no-op, bez wyjątku i bez zmiany stanu.
    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    assert conn.execute(
        "SELECT id FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone() is None
    total = conn.execute(
        "SELECT SUM(quantity) FROM sale_allocations WHERE sale_id = 1").fetchone()[0]
    assert total == pytest.approx(784.0)


def test_revert_is_noop_on_state_that_was_never_broken(conn):
    """Świeża baza (fantomowy lot nigdy nie istniał) — guard po `source =
    'manual_reconciliation'` musi zwrócić się natychmiast, nic nie ruszając."""
    ids = _setup_pre_fix_state(conn)

    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "pending"
    assert vest["lot_id"] is None
    total = conn.execute(
        "SELECT SUM(quantity) FROM sale_allocations WHERE sale_id = 1").fetchone()[0]
    assert total == pytest.approx(784.0)  # niezmieniona alokacja sprzed jakiejkolwiek naprawy


def test_apply_all_no_longer_reintroduces_the_lot(conn):
    """`apply_all()` woła już tylko `revert_...` + `link_pooled_...`, nie
    `fix_missing_...` — na stanie sprzed naprawy (transza wciąż 'pending', bez
    lotu, ale lot zbiorczy `batch2_id` już istnieje) `revert` musi zostać
    no-opem (nie dodać z powrotem 24.42 szt.), a `link_pooled` domyka transzę
    przez `pooled_lot_id` (E9) — to jest DOCELOWY stan świeżej instalacji, nie
    'pending' w nieskończoność."""
    ids = _setup_pre_fix_state(conn)

    data_fixes.apply_all(conn)

    assert conn.execute(
        "SELECT id FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone() is None
    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "vested"
    assert vest["lot_id"] is None
    assert vest["pooled_lot_id"] == ids["batch2_id"]


def test_apply_all_on_already_broken_production_shape_converges_to_clean(conn):
    """Mirror realnego startu add-onu na bazie, która już ma zastosowaną starą
    naprawę (dokładnie stan produkcyjny sprzed tego wydania) — jedno wywołanie
    `apply_all()` sprząta za jednym razem: cofa fantoma I domyka transzę przez
    `pooled_lot_id` (E9)."""
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    data_fixes.apply_all(conn)

    assert conn.execute(
        "SELECT id FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone() is None
    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "vested"
    assert vest["lot_id"] is None
    assert vest["pooled_lot_id"] == ids["batch2_id"]
    total = conn.execute(
        "SELECT SUM(quantity) FROM sale_allocations WHERE sale_id = 1").fetchone()[0]
    assert total == pytest.approx(784.0)


# --- 2026-08-24 (krok E9, docs/PLAN_E9_transza_w_puli.md): po cofnięciu naprawy
# powyżej transza wraca do 'pending'/lot_id=NULL — poprawny stan wg wyciągu, ale
# nierozróżnialny dla integrity.py od realnej luki (brakujący import). Ta naprawa
# domyka transzę na `batch2_id` ('vested_release:2025-08-28:3.71:101.396662',
# ten sam lot zbiorczy) przez `pooled_lot_id`, zamiast dokładać kolejny fantomowy
# lot — zero zmian w lots/sale_allocations/sales. ---


def test_link_pooled_marks_vest_vested_with_pooled_lot_id(conn):
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)
    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    data_fixes.link_pooled_espp_match_2025_08(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "vested"
    assert vest["lot_id"] is None
    assert vest["pooled_lot_id"] == ids["batch2_id"]


def test_link_pooled_touches_nothing_else(conn):
    """Zero zmian w lots/sale_allocations/sales — wartość tej transzy jest już
    policzona w locie zbiorczym, nie duplikujemy jej nigdzie."""
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)
    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)
    lots_before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM lots").fetchall()}
    allocs_before = [dict(r) for r in conn.execute(
        "SELECT * FROM sale_allocations ORDER BY id").fetchall()]

    data_fixes.link_pooled_espp_match_2025_08(conn)

    lots_after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM lots").fetchall()}
    allocs_after = [dict(r) for r in conn.execute(
        "SELECT * FROM sale_allocations ORDER BY id").fetchall()]
    assert lots_after == lots_before
    assert allocs_after == allocs_before
    sale = conn.execute("SELECT * FROM sales WHERE id = ?", (ids["sale_id"],)).fetchone()
    assert sale["reported_cost_pln"] == pytest.approx(7500.66)
    assert sale["reported_revenue_pln"] == pytest.approx(17631.72)


def test_link_pooled_is_idempotent(conn):
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)
    data_fixes.revert_phantom_espp_match_lot_2025_08(conn)

    data_fixes.link_pooled_espp_match_2025_08(conn)
    data_fixes.link_pooled_espp_match_2025_08(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "vested"
    assert vest["pooled_lot_id"] == ids["batch2_id"]


def test_link_pooled_is_noop_without_the_pooled_lot(conn):
    """Baza bez batch2_id (nigdy nie zaimportowano wyciągu 2025-08-28) — nie
    zgadujemy, transza zostaje nietknięta."""
    ids = _setup_pre_fix_state(conn)
    conn.execute("DELETE FROM sale_allocations WHERE lot_id = ?", (ids["batch2_id"],))
    conn.execute("DELETE FROM lots WHERE id = ?", (ids["batch2_id"],))
    conn.commit()

    data_fixes.link_pooled_espp_match_2025_08(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "pending"
    assert vest["pooled_lot_id"] is None


def test_apply_all_links_pooled_vest_end_to_end(conn):
    """Mirror realnego startu add-onu 0.25.0 na stanie produkcyjnym sprzed tego
    wydania (stara błędna naprawa jeszcze zastosowana) — jedno `apply_all()`
    cofa fantoma I domyka transzę przez pooled_lot_id, w tej kolejności."""
    ids = _setup_pre_fix_state(conn)
    data_fixes.fix_missing_espp_match_lot_2025_08(conn)

    data_fixes.apply_all(conn)

    vest = conn.execute("SELECT * FROM vests WHERE id = ?", (ids["vest_id"],)).fetchone()
    assert vest["status"] == "vested"
    assert vest["lot_id"] is None
    assert vest["pooled_lot_id"] == ids["batch2_id"]
    assert conn.execute(
        "SELECT id FROM lots WHERE natural_key = 'vested_matching:2025-08-28:3.71:24.42'"
    ).fetchone() is None
