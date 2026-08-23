"""E6 krok 4 (docs/PLAN_E6_wyplata.md) — `views/withdrawal.py::withdrawal_view`,
klocek zasilający /wyplata. Sprawdza WYŁĄCZNIE kompozycję (kompletny kształt
wyniku, ten sam dla obu kierunków, obsługa błędu bez wyjątku) — poprawność
samej matematyki jest już pokryta przez `test_tax_whatif.py`
(`solve_for_net`/`annual_net_for_quantity`)."""
from __future__ import annotations

import pytest

from nokia_tracker import settings as settingsm
from nokia_tracker.tax import lots
from nokia_tracker.views.withdrawal import withdrawal_view


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


_KEYS = (
    "direction", "sale_date", "quantity", "whole_shares", "price_eur", "fee_pct",
    "gross_eur", "revenue_pln", "cost_fifo_pln", "income_pln", "usable_loss_pln",
    "tax_pln", "net_pln", "forfeit_qty", "forfeit_value_pln", "concentration_before",
    "concentration_after", "lots_consumed_detailed", "timing",
)


def test_withdrawal_view_direction_target_has_complete_shape(conn):
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)

    result, error = withdrawal_view(
        conn, cfg, "target", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        target_net_pln=1000.0)

    assert error is None
    for key in _KEYS:
        assert key in result
    assert result["direction"] == "target"
    assert result["quantity"] > 0


def test_withdrawal_view_direction_quantity_has_complete_shape(conn):
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)

    result, error = withdrawal_view(
        conn, cfg, "quantity", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        quantity=10.0)

    assert error is None
    assert result["quantity"] == pytest.approx(10.0)
    assert result["whole_shares"] is None  # tylko kierunek A ma pełne-akcje


def test_withdrawal_view_two_directions_agree_on_net_for_same_quantity(conn):
    # Kierunek A trafiający dokładnie na netto osiągalne z 10 akcji powinien
    # zwrócić TĘ SAMĄ ilość i netto co kierunek B poproszony wprost o 10 akcji —
    # obie ścieżki kończą w annual_net_for_quantity (dowód braku dwóch silników).
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)

    direct, _ = withdrawal_view(
        conn, cfg, "quantity", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        quantity=10.0)
    via_target, _ = withdrawal_view(
        conn, cfg, "target", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        target_net_pln=direct["net_pln"])

    assert via_target["quantity"] == pytest.approx(10.0, abs=0.01)
    assert via_target["net_pln"] == pytest.approx(direct["net_pln"], abs=1.0)


def test_withdrawal_view_returns_error_not_exception_when_target_unreachable(conn):
    lots.add_lot(conn, "2020-01-01", "own", 5.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)

    result, error = withdrawal_view(
        conn, cfg, "target", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        target_net_pln=1_000_000.0)

    assert result is None
    assert error is not None


def test_withdrawal_view_returns_error_not_exception_when_no_open_lots(conn):
    cfg = settingsm.get_settings(conn)

    result, error = withdrawal_view(
        conn, cfg, "quantity", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        quantity=10.0)

    assert result is None
    assert error is not None


def test_withdrawal_view_fee_pct_reduces_net_for_quantity_direction(conn):
    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")
    cfg = settingsm.get_settings(conn)

    no_fee, _ = withdrawal_view(
        conn, cfg, "quantity", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        quantity=10.0)
    with_fee, _ = withdrawal_view(
        conn, cfg, "quantity", price_eur=8.0, fee_pct=1.0, sale_date="2026-07-28",
        quantity=10.0)

    assert with_fee["net_pln"] < no_fee["net_pln"]


def test_withdrawal_view_future_sale_date_beyond_nbp_returns_error_not_500(
        conn, monkeypatch):
    # Znalezisko z weryfikacji produkcyjnej 0.22.0: NBP zwraca HTTP 400 dla
    # dat PRZYSZŁYCH (fx_nbp.py docstring) — data sprzedaży daleko w
    # przyszłości (np. wybór miesiąca w kalkulatorze) nie ma jeszcze kursu.
    from nokia_tracker.providers.base import QuoteProviderError

    lots.add_lot(conn, "2020-01-01", "own", 100.0, 3.0, source="manual")

    def _raise(conn, event_date):
        raise QuoteProviderError(f"NBP {event_date}: HTTP 400")

    monkeypatch.setattr("nokia_tracker.tax.whatif.fx_nbp.rate_for_event", _raise)

    result, error = withdrawal_view(
        conn, settingsm.get_settings(conn), "quantity", price_eur=8.0, fee_pct=0.0,
        sale_date="2027-06-01", quantity=10.0)

    assert result is None
    assert error is not None
    assert "NBP" in error


def test_withdrawal_view_forfeit_not_subtracted_from_net_pln(conn):
    # E4-lekcja: przepadek to utrata akcji, nie przepływ gotówki — nie może być
    # wliczony do net_pln (patrz PLAN_E6_wyplata.md, krok 4).
    from nokia_tracker.tax import grants

    lots.add_lot(conn, "2025-10-27", "own", 29.24, 5.41, source="pdf_import")
    grant_id = grants.add_grant(conn, "espp", "2025-10-27", 29.24, "espp_grant:x")
    grants.add_vest(
        conn, grant_id, "2026-08-01", 29.24, "espp_vest:x", available_from="2026-08-27")
    cfg = settingsm.get_settings(conn)

    result, error = withdrawal_view(
        conn, cfg, "quantity", price_eur=8.0, fee_pct=0.0, sale_date="2026-07-28",
        quantity=29.24)

    assert error is None
    assert result["forfeit_value_pln"] > 0
    assert result["net_pln"] == pytest.approx(
        result["revenue_pln"] - result["tax_pln"], abs=0.01)
