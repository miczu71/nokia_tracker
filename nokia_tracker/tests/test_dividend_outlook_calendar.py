"""calendar() — scalanie zdarzeń (ogłoszony harmonogram + szacowane) i łańcuch
podatkowy sekcji G (krok 30, docs/PLAN_KROK_30_dywidendy.md, commit 3/8).

`per_share_history`/`entitled_base`/`qty_on` są pokryte osobno w
test_dividend_outlook.py — tutaj testujemy tylko SCALANIE i reużycie łańcucha
podatkowego, zero nowej matematyki podatkowej (to pilnuje
`test_tax_chain_matches_section_g_for_identical_inputs`)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nokia_tracker import dividend_outlook as outlook
from nokia_tracker.tax import dividends as taxdiv, grants as grantsm, lots as taxlots


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _cfg(**overrides) -> dict:
    cfg = {
        "finnish_withholding_pct": 35.0, "treaty_withholding_pct": 15.0,
        "pl_capital_gains_tax_pct": 19.0,
    }
    cfg.update(overrides)
    return cfg


def _real_dividend(conn, pay_date, entitled_qty, per_share_eur, withholding_pct=35.0):
    gross_eur = entitled_qty * per_share_eur
    taxes_eur = gross_eur * withholding_pct / 100
    taxdiv.add_dividend(conn, record_date=pay_date, entitled_quantity=entitled_qty,
                        gross_eur=gross_eur, taxes_eur=taxes_eur, fees_eur=0.0)


def _seed_four_quarterly(conn, per_share_eur=0.04):
    for pay_date in ["2025-02-20", "2025-05-15", "2025-08-14", "2025-11-13"]:
        _real_dividend(conn, pay_date, 100.0, per_share_eur)


def _add_schedule_row(conn, fiscal_year, instalment, record_date, per_share_eur,
                      dates_confirmed=0):
    conn.execute(
        "INSERT INTO dividend_schedule (fiscal_year, instalment, record_date, "
        "gross_per_share_eur, dates_confirmed) VALUES (?,?,?,?,?)",
        (fiscal_year, instalment, record_date, per_share_eur, dates_confirmed))
    conn.commit()


# --- honesty contract: brak wystarczającej historii -> zero zdarzeń szacowanych ---

def test_insufficient_history_emits_no_estimated_events_and_a_reason(conn):
    _real_dividend(conn, "2026-01-30", 100.0, 0.04)  # tylko 1 realna wypłata

    result = outlook.calendar(conn, _cfg(), today="2026-06-01")

    assert result["events"] == []
    assert result["assumptions"]["sufficient_history"] is False
    assert result["assumptions"]["reason"]


def test_insufficient_history_still_shows_confirmed_schedule_rows(conn):
    _real_dividend(conn, "2026-01-30", 100.0, 0.04)
    _add_schedule_row(conn, 2026, 2, "2026-07-25", 0.04, dates_confirmed=1)

    result = outlook.calendar(conn, _cfg(), today="2026-06-01")

    assert len(result["events"]) == 1
    assert result["events"][0]["certainty"] == "confirmed"


# --- scalanie: rata ogłoszona wypiera zdarzenie szacowane w tym samym slocie ---

def test_schedule_row_suppresses_estimated_event_for_same_quarter(conn):
    _seed_four_quarterly(conn)
    # Kolejna rata wypadałaby szacunkowo ~2026-02, ogłoszona rata na 2026-02-18
    # w tym samym kwartale powinna ją wyprzeć, nie zduplikować.
    _add_schedule_row(conn, 2026, 1, "2026-02-18", 0.04, dates_confirmed=1)

    result = outlook.calendar(conn, _cfg(), today="2025-12-01", years_ahead=1)

    q1_2026_events = [e for e in result["events"] if e["record_date"][:7] == "2026-02"
                      or (e["record_date"] >= "2026-01-01" and e["record_date"] < "2026-04-01")]
    assert len(q1_2026_events) == 1
    assert q1_2026_events[0]["certainty"] == "confirmed"
    assert q1_2026_events[0]["record_date"] == "2026-02-18"


def test_matched_schedule_row_is_not_projected_forward(conn):
    _seed_four_quarterly(conn)
    _add_schedule_row(conn, 2026, 1, "2026-02-18", 0.04, dates_confirmed=1)
    conn.execute("UPDATE dividend_schedule SET matched_dividend_id = 1 "
                 "WHERE fiscal_year = 2026 AND instalment = 1")
    conn.commit()

    result = outlook.calendar(conn, _cfg(), today="2025-12-01", years_ahead=1)

    dates = [e["record_date"] for e in result["events"]]
    assert "2026-02-18" not in dates


# --- łańcuch sekcji G reużyty bez nowej matematyki ---

def test_tax_chain_matches_section_g_for_identical_inputs(conn):
    """`calendar()` musi liczyć podatek PRZEZ `taxdiv.compute_dividend_tax`/
    `compute_dividend_tax_pln` — te same funkcje, które sekcja G PIT-38 już używa —
    nie przez nową matematykę. Sprawdzone porównaniem z bezpośrednim wywołaniem tych
    samych funkcji na tych samych wejściach (500 akcji x 0,05 EUR, kurs 4,3)."""
    cfg = _cfg()
    taxlots.add_lot(conn, "2020-01-01", "own", 500.0, 5.0)
    _add_schedule_row(conn, 2026, 1, "2026-03-01", 0.05, dates_confirmed=1)

    result = outlook.calendar(conn, cfg, eurpln_rate=4.3, today="2026-01-01")
    event = result["events"][0]

    gross_eur = 500.0 * 0.05
    withholding_pct = cfg["finnish_withholding_pct"]
    expected_eur = taxdiv.compute_dividend_tax(
        gross_eur, withholding_pct, cfg["treaty_withholding_pct"],
        cfg["pl_capital_gains_tax_pct"])
    expected_pln = taxdiv.compute_dividend_tax_pln(
        {"gross_pln": gross_eur * 4.3, "withholding_pct": withholding_pct}, cfg)

    assert event["gross_eur"] == pytest.approx(gross_eur)
    assert event["withholding_paid_eur"] == pytest.approx(expected_eur["withholding_paid_eur"])
    assert event["pl_tax_due_eur"] == pytest.approx(expected_eur["pl_tax_due_eur"])
    assert event["gross_pln"] == pytest.approx(gross_eur * 4.3)
    assert event["pl_tax_due_pln"] == pytest.approx(expected_pln["pl_tax_due_pln"])


def test_pln_keys_are_none_without_fx_rate_and_no_keyerror(conn):
    _seed_four_quarterly(conn)

    result = outlook.calendar(conn, _cfg(), eurpln_rate=None, today="2025-12-01")

    assert result["events"], "oczekiwano co najmniej jednego zdarzenia szacowanego"
    for e in result["events"]:
        assert e["gross_pln"] is None
        assert e["pl_tax_due_pln"] is None
        assert e["net_in_hand_pln"] is None


def test_net_in_hand_excludes_reclaimable_from_finland(conn):
    _seed_four_quarterly(conn)

    result = outlook.calendar(conn, _cfg(), today="2025-12-01")
    e = result["events"][0]

    expected_net = e["gross_eur"] - e["withholding_paid_eur"] - e["pl_tax_due_eur"]
    assert e["net_in_hand_eur"] == pytest.approx(expected_net)
    # reclaimable NIE jest odjęte/dodane do net_in_hand - zostaje osobną linią
    assert "reclaimable_from_finland_eur" in e


# --- regresja: po co ta fala w ogóle istnieje ---

def test_projection_reflects_vesting_jump(conn):
    """120 posiadanych akcji dziś + transza 2800 wchodząca w horyzoncie -> zdarzenie
    PO vestingu jest wielokrotnie większe od zdarzenia PRZED nim. To jest dokładnie
    sytuacja z produkcji (119.66 -> 2888.66 akcji), tu na liczbach syntetycznych."""
    taxlots.add_lot(conn, "2020-01-01", "own", 120.0, 5.0)
    _seed_four_quarterly(conn)
    grant_id = grantsm.add_grant(conn, "espp", "2024-01-01", quantity=None,
                                 natural_key="g_jump")
    grantsm.add_vest(conn, grant_id, vest_date="2026-03-01", quantity=2800.0,
                     natural_key="v_jump", status="pending",
                     available_from="2026-03-01")

    result = outlook.calendar(conn, _cfg(), today="2025-12-01", years_ahead=1)

    events = result["events"]
    assert len(events) >= 2
    before = [e for e in events if e["record_date"] < "2026-03-01"]
    after = [e for e in events if e["record_date"] >= "2026-03-01"]
    assert before and after
    assert after[0]["entitled_qty"] > before[-1]["entitled_qty"] * 5


def test_estimated_record_date_snaps_off_weekend(conn):
    # 91 dni to dokładnie 13 tygodni (91 = 13*7) - dzień tygodnia się nie przesuwa
    # między wypłatami. Wszystkie 4 realne wypłaty poniżej wypadają w sobotę
    # (2025-01-04), więc rzutowana kolejna (2025-01-04 + 4*91 dni) też wypadnie
    # w sobotę - musi zostać zrzucona na piątek.
    for pay_date in ["2025-01-04", "2025-04-05", "2025-07-05", "2025-10-04"]:
        _real_dividend(conn, pay_date, 100.0, 0.04)

    result = outlook.calendar(conn, _cfg(), today="2025-11-01", years_ahead=1)

    for e in result["events"]:
        d = datetime.strptime(e["record_date"], "%Y-%m-%d")
        assert d.weekday() < 5  # nigdy sobota (5) ani niedziela (6)


def test_horizon_years_limits_event_count(conn):
    _seed_four_quarterly(conn)

    short = outlook.calendar(conn, _cfg(), years_ahead=1, today="2025-12-01")
    longer = outlook.calendar(conn, _cfg(), years_ahead=3, today="2025-12-01")

    assert len(longer["events"]) > len(short["events"])
    horizon_cutoff = (datetime.strptime("2025-12-01", "%Y-%m-%d")
                      + timedelta(days=365)).strftime("%Y-%m-%d")
    assert all(e["record_date"] <= horizon_cutoff for e in short["events"])


def test_today_hook_makes_output_deterministic(conn):
    _seed_four_quarterly(conn)

    r1 = outlook.calendar(conn, _cfg(), today="2025-12-01")
    r2 = outlook.calendar(conn, _cfg(), today="2025-12-01")

    assert [e["record_date"] for e in r1["events"]] == [e["record_date"] for e in r2["events"]]


def test_next_event_and_ntm_are_populated(conn):
    _seed_four_quarterly(conn)

    result = outlook.calendar(conn, _cfg(), today="2025-12-01")

    assert result["next_event"] is not None
    assert result["next_event"]["record_date"] == result["events"][0]["record_date"]
    assert result["ntm_gross_eur"] >= result["next_event"]["gross_eur"]


def test_calendar_empty_db_returns_empty_events_not_a_crash(conn):
    result = outlook.calendar(conn, _cfg(), today="2026-01-01")

    assert result["events"] == []
    assert result["next_event"] is None
    assert result["ntm_gross_eur"] == 0.0


def test_ntm_net_in_hand_pln_is_none_without_fx_rate(conn):
    _seed_four_quarterly(conn)

    result = outlook.calendar(conn, _cfg(), eurpln_rate=None, today="2025-12-01")

    assert result["ntm_net_in_hand_pln"] is None


def test_ntm_net_in_hand_pln_sums_events_with_fx_rate(conn):
    _seed_four_quarterly(conn)

    result = outlook.calendar(conn, _cfg(), eurpln_rate=4.0, today="2025-12-01")

    expected = sum(e["net_in_hand_pln"] for e in result["events"]
                   if e["record_date"] <= "2026-12-01")
    assert result["ntm_net_in_hand_pln"] == pytest.approx(expected)
