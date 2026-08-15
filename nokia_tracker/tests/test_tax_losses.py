"""Straty z lat ubiegłych i zamknięcie roku podatkowego (krok 27,
docs/PLAN_KROK_27_straty_kreator.md). Zero żywego HTTP — fx_nbp.rate_for_event
zamockowane na stały kurs, jak w test_tax_pit38.py/test_tax_lots.py."""
from __future__ import annotations

import pytest

from nokia_tracker.tax import lots, losses
from nokia_tracker.tax import pit38


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _base_cfg(**overrides) -> dict:
    cfg = {"cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0}
    cfg.update(overrides)
    return cfg


def _loss_year(conn, year: int, quantity=10, buy_price=10.0, sell_price=5.0):
    """Tworzy sprzedaż stratną w danym roku (own lot, sprzedane taniej)."""
    lots.add_lot(conn, f"{year}-01-10", "own", quantity, buy_price)
    lots.record_sale(conn, f"{year}-06-01", quantity, sell_price)


def _profit_year(conn, year: int, quantity=10, buy_price=5.0, sell_price=10.0):
    lots.add_lot(conn, f"{year}-01-10", "own", quantity, buy_price)
    lots.record_sale(conn, f"{year}-06-01", quantity, sell_price)


# ---------------------------------------------------------------- rebuild() ----

def test_rebuild_creates_row_for_loss_year(conn):
    _loss_year(conn, 2020)
    result = losses.rebuild(conn, _base_cfg())

    row = conn.execute(
        "SELECT * FROM tax_loss_carryforward WHERE origin_year=2020 AND cost_basis_policy='own_only'"
    ).fetchone()
    assert row is not None
    # buy 10*10=100 EUR * 4.0 = 400 PLN cost, sell 10*5=50 EUR*4.0=200 PLN revenue -> loss 200
    assert row["loss_pln"] == pytest.approx(200.0)
    assert (2020, "own_only") in result["upserted"]


def test_rebuild_does_not_create_row_for_profit_year(conn):
    _profit_year(conn, 2021)
    losses.rebuild(conn, _base_cfg())

    row = conn.execute(
        "SELECT * FROM tax_loss_carryforward WHERE origin_year=2021"
    ).fetchone()
    assert row is None


def test_rebuild_three_policies_differ_for_same_sale(conn):
    # own/matched/dividend_drip lots o różnych, niezerowych kosztach w tej samej
    # sprzedaży -> trzy polityki (różny zestaw uznawanych typów lotu) dają trzy
    # różne loss_pln.
    lots.add_lot(conn, "2022-01-10", "own", 10, 10.0)
    lots.add_lot(conn, "2022-01-11", "matched", 10, 1.0)
    lots.add_lot(conn, "2022-01-12", "dividend_drip", 10, 3.0)
    lots.record_sale(conn, "2022-06-01", 30, 2.0)

    losses.rebuild(conn, _base_cfg())

    rows = {
        r["cost_basis_policy"]: r["loss_pln"]
        for r in conn.execute(
            "SELECT cost_basis_policy, loss_pln FROM tax_loss_carryforward WHERE origin_year=2022"
        ).fetchall()
    }
    # revenue = 30*2*4.0 = 240 PLN dla każdej polityki
    # own_only: koszt = 10*10*4=400 -> strata 160
    assert rows["own_only"] == pytest.approx(160.0)
    # own_plus_drip: koszt = 400 + 10*3*4=120 = 520 -> strata 280
    assert rows["own_plus_drip"] == pytest.approx(280.0)
    # all_at_acquisition: koszt = 400 + 10*1*4=40 + 120 = 560 -> strata 320
    assert rows["all_at_acquisition"] == pytest.approx(320.0)
    assert len({rows["own_only"], rows["own_plus_drip"], rows["all_at_acquisition"]}) == 3


def test_rebuild_second_call_without_data_change_is_noop(conn):
    _loss_year(conn, 2020)
    first = losses.rebuild(conn, _base_cfg())
    second = losses.rebuild(conn, _base_cfg())

    row1 = conn.execute(
        "SELECT loss_pln FROM tax_loss_carryforward WHERE origin_year=2020 AND cost_basis_policy='own_only'"
    ).fetchone()
    assert row1["loss_pln"] == pytest.approx(200.0)
    assert second["conflicts"] == []
    assert (2020, "own_only") in second["upserted"]


def test_rebuild_year_no_longer_loss_without_deductions_row_disappears(conn):
    lots.add_lot(conn, "2020-01-10", "own", 10, 10.0)
    lots.record_sale(conn, "2020-06-01", 10, 5.0)
    losses.rebuild(conn, _base_cfg())
    row = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020 AND cost_basis_policy='own_only'"
    ).fetchone()
    assert row is not None

    # korekta: dodatkowa sprzedaż zyskowna w tym samym roku zamienia rok na zyskowny
    lots.add_lot(conn, "2020-02-10", "own", 100, 1.0)
    lots.record_sale(conn, "2020-07-01", 100, 50.0)

    losses.rebuild(conn, _base_cfg())
    row_after = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020 AND cost_basis_policy='own_only'"
    ).fetchone()
    assert row_after is None


def test_rebuild_year_no_longer_loss_with_deductions_row_stays_with_conflict(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    row = conn.execute(
        "SELECT id, loss_pln FROM tax_loss_carryforward WHERE origin_year=2020 AND cost_basis_policy='own_only'"
    ).fetchone()
    loss_id = row["id"]

    # 2021 dochodowy, żeby móc odliczyć stratę z 2020
    _profit_year(conn, 2021, quantity=100, buy_price=1.0, sell_price=50.0)
    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 50.0)

    # korekta danych 2020: dodatkowa sprzedaż zyskowna zamienia rok w zyskowny
    lots.add_lot(conn, "2020-02-10", "own", 100, 1.0)
    lots.record_sale(conn, "2020-07-01", 100, 50.0)

    result = losses.rebuild(conn, _base_cfg())

    row_after = conn.execute(
        "SELECT id, loss_pln FROM tax_loss_carryforward WHERE id=?", (loss_id,)
    ).fetchone()
    assert row_after is not None
    assert row_after["loss_pln"] == pytest.approx(200.0)  # niezmieniona
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["origin_year"] == 2020


# ------------------------------------------------------------ available_for_year() ----

def test_available_for_year_window_n1_to_n5(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())

    for y in range(2021, 2026):
        avail = losses.available_for_year(conn, _base_cfg(), y)
        assert avail["total_remaining_pln"] == pytest.approx(200.0), f"year {y}"

    avail_2026 = losses.available_for_year(conn, _base_cfg(), 2026)
    assert avail_2026["total_remaining_pln"] == 0.0
    assert avail_2026["items"] == []


def test_available_for_year_two_losses_sum_in_n3(conn):
    _loss_year(conn, 2020)
    _loss_year(conn, 2022)
    losses.rebuild(conn, _base_cfg())

    avail = losses.available_for_year(conn, _base_cfg(), 2023)
    assert avail["total_remaining_pln"] == pytest.approx(400.0)
    assert len(avail["items"]) == 2


def test_available_for_year_used_before_reduces_remaining(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    row = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020 AND cost_basis_policy='own_only'"
    ).fetchone()
    loss_id = row["id"]

    _profit_year(conn, 2021, quantity=100, buy_price=1.0, sell_price=50.0)
    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 100.0)

    avail = losses.available_for_year(conn, _base_cfg(), 2022)
    item = avail["items"][0]
    assert item["used_before_pln"] == pytest.approx(100.0)
    assert item["remaining_pln"] == pytest.approx(100.0)


def test_available_for_year_no_rows_returns_zeroed(conn):
    avail = losses.available_for_year(conn, _base_cfg(), 2030)
    assert avail["total_remaining_pln"] == 0.0
    assert avail["items"] == []


# ------------------------------------------------------------ max_deduction_pln() ----

def test_max_deduction_full_amount_when_unused_and_under_cap():
    assert losses.max_deduction_pln(10000.0, 0.0, 10000.0) == pytest.approx(10000.0)


def test_max_deduction_capped_at_half_once_partially_used():
    assert losses.max_deduction_pln(10000.0, 3000.0, 7000.0) == pytest.approx(5000.0)


def test_max_deduction_large_loss_excludes_lump_sum():
    assert losses.max_deduction_pln(8_000_000.0, 0.0, 8_000_000.0) == pytest.approx(4_000_000.0)


# ------------------------------------------------------------ record_deduction() ----

def test_record_deduction_within_window_succeeds(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    loss_id = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020"
    ).fetchone()["id"]
    _profit_year(conn, 2021, quantity=100, buy_price=1.0, sell_price=50.0)

    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 200.0)

    used = conn.execute(
        "SELECT amount_pln FROM tax_loss_deductions WHERE loss_id=? AND used_in_year=2021",
        (loss_id,)).fetchone()
    assert used["amount_pln"] == pytest.approx(200.0)


def test_record_deduction_outside_window_raises(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    loss_id = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020"
    ).fetchone()["id"]
    _profit_year(conn, 2026, quantity=100, buy_price=1.0, sell_price=50.0)

    with pytest.raises(ValueError):
        losses.record_deduction(conn, _base_cfg(), loss_id, 2026, 100.0)


def test_record_deduction_over_max_raises(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    loss_id = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020"
    ).fetchone()["id"]
    _profit_year(conn, 2021, quantity=100, buy_price=1.0, sell_price=50.0)

    with pytest.raises(ValueError):
        losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 100000.0)


def test_record_deduction_over_income_raises(conn):
    # strata duża (2 mln PLN, max_deduction niski limit nie ogranicza), ale
    # dochód roku docelowego malutki -> to dochód ma być wiążącym ograniczeniem
    _loss_year(conn, 2020, quantity=100_000, buy_price=10.0, sell_price=5.0)
    losses.rebuild(conn, _base_cfg())
    loss_id = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020"
    ).fetchone()["id"]
    # mały dochód w 2021
    _profit_year(conn, 2021, quantity=10, buy_price=1.0, sell_price=2.0)  # income = 10*1*4=40

    with pytest.raises(ValueError):
        losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 1000.0)


def test_record_deduction_second_write_overwrites_not_duplicates(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    loss_id = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020"
    ).fetchone()["id"]
    _profit_year(conn, 2021, quantity=100, buy_price=1.0, sell_price=50.0)

    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 100.0)
    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 150.0)

    rows = conn.execute(
        "SELECT amount_pln FROM tax_loss_deductions WHERE loss_id=? AND used_in_year=2021",
        (loss_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["amount_pln"] == pytest.approx(150.0)


def test_record_deduction_zero_amount_deletes_entry(conn):
    _loss_year(conn, 2020)
    losses.rebuild(conn, _base_cfg())
    loss_id = conn.execute(
        "SELECT id FROM tax_loss_carryforward WHERE origin_year=2020"
    ).fetchone()["id"]
    _profit_year(conn, 2021, quantity=100, buy_price=1.0, sell_price=50.0)
    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 100.0)

    losses.record_deduction(conn, _base_cfg(), loss_id, 2021, 0.0)

    row = conn.execute(
        "SELECT * FROM tax_loss_deductions WHERE loss_id=? AND used_in_year=2021",
        (loss_id,)).fetchone()
    assert row is None


def test_record_deduction_loss_isolated_per_policy(conn):
    # lti lot ma realny koszt uznawany TYLKO pod all_at_acquisition — sprzedaż
    # jest stratna pod all_at_acquisition, ale zyskowna pod own_only/own_plus_drip
    # (ten koszt tam po prostu nie istnieje) -> strata nie powinna "przeciekać"
    # między politykami w tax_loss_carryforward/available_for_year.
    lots.add_lot(conn, "2020-01-10", "own", 10, 2.0)
    lots.add_lot(conn, "2020-01-11", "lti", 10, 5.0)
    lots.record_sale(conn, "2020-06-01", 20, 3.0)
    losses.rebuild(conn, _base_cfg())

    avail_own_only = losses.available_for_year(
        conn, _base_cfg(), 2021, policy="own_only")
    avail_all = losses.available_for_year(
        conn, _base_cfg(), 2021, policy="all_at_acquisition")

    assert avail_own_only["total_remaining_pln"] == 0.0
    assert avail_own_only["items"] == []
    # revenue 20*3*4=240, koszt own+lti=10*2*4+10*5*4=80+200=280 -> strata 40
    assert avail_all["total_remaining_pln"] == pytest.approx(40.0)


# ------------------------------------------------ close_year/reopen_year/is_year_closed ----

def test_close_year_saves_snapshot(conn):
    losses.close_year(conn, _base_cfg(), 2024, total_due_pln=1234.56)
    row = conn.execute(
        "SELECT total_due_pln_snapshot FROM tax_year_closed WHERE year=2024").fetchone()
    assert row["total_due_pln_snapshot"] == pytest.approx(1234.56)


def test_is_year_closed_toggles(conn):
    assert losses.is_year_closed(conn, 2024) is False
    losses.close_year(conn, _base_cfg(), 2024, total_due_pln=100.0)
    assert losses.is_year_closed(conn, 2024) is True
    losses.reopen_year(conn, 2024)
    assert losses.is_year_closed(conn, 2024) is False


def test_close_year_twice_overwrites_snapshot(conn):
    losses.close_year(conn, _base_cfg(), 2024, total_due_pln=100.0)
    losses.close_year(conn, _base_cfg(), 2024, total_due_pln=200.0)
    row = conn.execute(
        "SELECT total_due_pln_snapshot FROM tax_year_closed WHERE year=2024").fetchone()
    assert row["total_due_pln_snapshot"] == pytest.approx(200.0)


# ------------------------------------------------------------ wizard_steps() ----

def test_wizard_steps_import_false_when_no_import_in_year(conn):
    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    steps = losses.wizard_steps(conn, _base_cfg(), 2024, report=report)
    by_key = {s["key"]: s for s in steps}
    assert by_key["import"]["done"] is False


def test_wizard_steps_unresolved_balance_conflict_blocks_conflicts_and_balance(conn):
    cur = conn.execute(
        "INSERT INTO imports (filename, file_sha256) VALUES ('x.pdf', 'abc')")
    import_id = cur.lastrowid
    conn.execute(
        "INSERT INTO import_conflicts (import_id, entity_type, natural_key, "
        "existing_json, incoming_json, resolved) VALUES (?, 'balance', 'k1', '{}', '{}', 0)",
        (import_id,))
    conn.commit()

    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    steps = losses.wizard_steps(conn, _base_cfg(), 2024, report=report)
    by_key = {s["key"]: s for s in steps}
    assert by_key["conflicts"]["done"] is False
    assert by_key["balance"]["done"] is False


def test_wizard_steps_losses_true_when_no_available_losses(conn):
    report = pit38.annual_report(conn, _base_cfg(), year=2024)
    steps = losses.wizard_steps(conn, _base_cfg(), 2024, report=report)
    by_key = {s["key"]: s for s in steps}
    assert by_key["losses"]["done"] is True
