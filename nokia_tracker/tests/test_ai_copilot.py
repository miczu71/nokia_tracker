"""ai/copilot.py — asystent proaktywny (krok 33, docs/PLAN_KROK_33_copilot.md).

Jeden dzienny push spinający trzy już policzone warunki (vesting / strata z
lat ubiegłych + zysk w tym roku / dywidenda) w JEDNĄ złączoną wiadomość,
narrowaną przez AI #2 — ale wiadomość ZAWSZE zawiera deterministyczne zdania
silnika, niezależnie czy narracja AI się powiodła (w odróżnieniu od czatu,
gdzie `answer_pl` siedzi obok renderowanej tabelki — tu push to sam tekst).

Zero żywego HTTP/AI — `provider.analyze` mockowane bezpośrednio (wzorzec z
`test_ai_chat.py`), `ha_client.notify` monkeypatchowane (wzorzec z
`test_notifier.py`/`test_alerts.py`). `fx_nbp.rate_for_event` zamockowane
tak jak w `test_tax_policy.py`/`test_tax_losses.py`, bo `lots.add_lot`/
`record_sale` (użyte do seedowania zysku/lotów) go wołają."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from nokia_tracker import alerts, dividend_outlook as outlookm, fx, ha_client
from nokia_tracker.ai import copilot, provider
from nokia_tracker.ai.errors import AIProviderError
from nokia_tracker.tax import grants as grantsm
from nokia_tracker.tax import lots

TODAY = "2026-08-16"


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _cfg(**overrides):
    base = {
        "copilot_enabled": 1, "copilot_min_interval_days": 30,
        "notify_service": "notify.family",
        "ai_chat_narration_enabled": 1,
        "ai_primary": "local", "ai_fallback": "gemini",
        "local_llm_base_url": "http://x/v1", "local_llm_api_key": "lkey",
        "local_llm_model": "m", "gemini_api_key": "gkey", "gemini_model": "m",
        "anthropic_api_key": "", "anthropic_model": "m",
        "ai_max_calls_per_day": 40, "ai_max_calls_per_day_local": 500,
        "cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0,
        "treaty_withholding_pct": 15.0, "finnish_withholding_pct": 35.0,
        "vest_reminder_days": 7,
    }
    base.update(overrides)
    return base


def _seed_vest(conn, vest_date, qty=12.5, key="v1", status="pending"):
    gid = grantsm.add_grant(conn, "lti", "2026-01-01", 100.0, f"g-{key}")
    grantsm.add_vest(conn, gid, vest_date, qty, key, status=status)
    return gid


def _seed_loss(conn, amount=200.0, origin_year=2024, policy="own_only"):
    conn.execute(
        "INSERT INTO tax_loss_carryforward (origin_year, cost_basis_policy, loss_pln) "
        "VALUES (?,?,?)", (origin_year, policy, amount))
    conn.commit()


def _seed_gain(conn, year=2026, qty=10, buy=5.0, sell=8.0):
    lots.add_lot(conn, f"{year}-01-10", "own", qty, buy)
    lots.record_sale(conn, f"{year}-06-01", qty, sell)


def _seed_open_lot(conn, acquired_date="2020-01-01", qty=100.0, price=5.0):
    lots.add_lot(conn, acquired_date, "own", qty, price)


def _seed_dividend_schedule(conn, record_date, fiscal_year=2026, instalment=1,
                            per_share=0.5, dates_confirmed=False):
    outlookm.add_instalment(conn, fiscal_year, instalment, record_date, per_share,
                            dates_confirmed=dates_confirmed)


def _seed_eurpln_quote(conn, rate=4.3, ts="2026-08-16T00:00:00Z"):
    conn.execute(
        "INSERT INTO instruments (symbol, name, currency, role) "
        "VALUES (?, 'EUR/PLN', 'PLN', 'fx')", (fx.EURPLN_SYMBOL,))
    iid = conn.execute(
        "SELECT id FROM instruments WHERE symbol = ?", (fx.EURPLN_SYMBOL,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO quotes (instrument_id, ts, granularity, close) VALUES (?, ?, 'daily', ?)",
        (iid, ts, rate))
    conn.commit()


def _fake_narration(monkeypatch, answer="Sprawdź portfel Nokii dzisiaj."):
    calls = []

    def _fake_analyze(conn, cfg, task, prompt, schema, max_tokens):
        calls.append(task)
        if task == "copilot_narration":
            return {"answer_pl": answer}
        raise AssertionError(f"unexpected task {task}")
    monkeypatch.setattr(provider, "analyze", _fake_analyze)
    return calls


def _fail_narration(monkeypatch, exc=None):
    def _boom(*a, **kw):
        raise exc or AIProviderError("wszystkie ogniwa padły")
    monkeypatch.setattr(provider, "analyze", _boom)


def _forbid_ai(monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("AI nie powinno być wołane")
    monkeypatch.setattr(provider, "analyze", _boom)


def _notify_spy(monkeypatch, result=True):
    calls = []
    monkeypatch.setattr(
        ha_client, "notify",
        lambda *a, **k: (calls.append((a, k)), result)[1])
    return calls


# ============================================================
# A. detekcja warunków (detect / check_*)
# ============================================================

def test_detect_returns_empty_on_empty_db(conn):
    assert copilot.detect(conn, _cfg(), today=TODAY) == []


def test_vesting_condition_fires_within_lookahead(conn):
    _seed_vest(conn, "2026-09-05")  # 20 dni
    result = copilot.check_vesting(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is not None
    assert result["kind"] == copilot.KIND_VESTING


def test_vesting_condition_silent_beyond_lookahead(conn):
    _seed_vest(conn, "2026-09-30")  # 45 dni
    result = copilot.check_vesting(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is None


def test_vesting_condition_silent_when_no_pending_tranche(conn):
    _seed_vest(conn, "2026-09-05", status="vested")
    result = copilot.check_vesting(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is None


def test_vesting_sentence_matches_dashboard_insights_wording(conn):
    _seed_vest(conn, "2026-09-05", qty=12.5)
    from nokia_tracker import dashboard_insights
    result = copilot.check_vesting(conn, _cfg(), copilot._context(conn, TODAY))
    expected = dashboard_insights.today_worth_knowing(
        change_pct_day=None, next_vest_date="2026-09-05", next_vest_qty=12.5,
        loss_available_pln=0.0, income_pln_this_year=0.0, today=TODAY)[0]
    assert result["sentence"] == expected


def test_vesting_condition_does_not_consume_vest_reminder_queue(conn):
    _seed_vest(conn, "2026-09-05", key="v1")
    copilot.detect(conn, _cfg(), today=TODAY)
    row = conn.execute(
        "SELECT reminder_sent_at FROM vests WHERE natural_key = 'v1'").fetchone()
    assert row["reminder_sent_at"] is None


def test_tax_loss_condition_silent_with_loss_but_no_income(conn):
    _seed_loss(conn)
    result = copilot.check_tax_loss(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is None


def test_tax_loss_condition_silent_with_income_but_no_loss(conn):
    _seed_gain(conn, year=2026)
    result = copilot.check_tax_loss(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is None


def test_tax_loss_condition_fires_when_both_present(conn):
    _seed_loss(conn)
    _seed_gain(conn, year=2026)
    result = copilot.check_tax_loss(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is not None
    assert result["kind"] == copilot.KIND_TAX_LOSS


def test_tax_loss_sentence_matches_dashboard_insights_wording(conn):
    _seed_loss(conn, amount=200.0)
    _seed_gain(conn, year=2026)
    from nokia_tracker import dashboard_insights
    from nokia_tracker.tax import losses as taxlosses, policy as taxpolicy
    cfg = _cfg()
    ctx = copilot._context(conn, TODAY)
    result = copilot.check_tax_loss(conn, cfg, ctx)
    loss = taxlosses.available_for_year(conn, cfg, 2026)["total_remaining_pln"]
    income = taxpolicy.compute_all_policies(conn, cfg, 2026)["own_only"]["income_pln"]
    expected = dashboard_insights.today_worth_knowing(
        change_pct_day=None, next_vest_date=None, next_vest_qty=None,
        loss_available_pln=loss, income_pln_this_year=income, today=TODAY)[0]
    assert result["sentence"] == expected


def test_tax_loss_uses_calendar_year_not_cfg_tax_year(conn):
    _seed_loss(conn)
    _seed_gain(conn, year=2026)
    result = copilot.check_tax_loss(conn, _cfg(tax_year=2020), copilot._context(conn, TODAY))
    assert result is not None  # policzone dla 2026 (rok TODAY), nie dla cfg["tax_year"]


def test_dividend_condition_fires_within_lookahead(conn):
    _seed_open_lot(conn)
    _seed_dividend_schedule(conn, "2026-08-30")  # 14 dni
    result = copilot.check_dividend(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is not None
    assert result["kind"] == copilot.KIND_DIVIDEND


def test_dividend_condition_silent_beyond_lookahead(conn):
    _seed_open_lot(conn)
    _seed_dividend_schedule(conn, "2026-10-15")  # 60 dni
    result = copilot.check_dividend(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is None


def test_dividend_condition_silent_without_open_position_or_schedule(conn):
    result = copilot.check_dividend(conn, _cfg(), copilot._context(conn, TODAY))
    assert result is None


def test_dividend_sentence_states_certainty(conn):
    _seed_open_lot(conn)
    _seed_eurpln_quote(conn)
    _seed_dividend_schedule(conn, "2026-08-30", dates_confirmed=False)
    result = copilot.check_dividend(conn, _cfg(), copilot._context(conn, TODAY))
    assert "zapowiedziana" in result["sentence"]
    assert "zł" in result["sentence"]


def test_dividend_sentence_falls_back_to_eur_without_fx(conn):
    _seed_open_lot(conn)
    _seed_dividend_schedule(conn, "2026-08-30", dates_confirmed=True)
    # celowo BEZ _seed_eurpln_quote -> eurpln_rate is None
    result = copilot.check_dividend(conn, _cfg(), copilot._context(conn, TODAY))
    assert "potwierdzona" in result["sentence"]
    assert "EUR" in result["sentence"]
    assert "zł" not in result["sentence"]


def test_detect_returns_all_three_in_fixed_order(conn):
    _seed_vest(conn, "2026-09-05")
    _seed_loss(conn)
    _seed_gain(conn, year=2026)
    _seed_open_lot(conn)
    _seed_dividend_schedule(conn, "2026-08-30")
    kinds = [c["kind"] for c in copilot.detect(conn, _cfg(), today=TODAY)]
    assert kinds == [copilot.KIND_VESTING, copilot.KIND_TAX_LOSS, copilot.KIND_DIVIDEND]


# ============================================================
# B. anti-spam (per kind, przez alerts_log)
# ============================================================

def test_run_writes_one_alerts_log_row_per_fired_kind(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _seed_loss(conn)
    _seed_gain(conn, year=2026)
    _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "sent"
    kinds = {r["kind"] for r in conn.execute("SELECT kind FROM alerts_log").fetchall()}
    assert kinds == {copilot.KIND_VESTING, copilot.KIND_TAX_LOSS}


def test_run_suppresses_condition_within_cooldown(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    copilot.run(conn, _cfg(), today=TODAY)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "cooldown"
    assert len(calls) == 1


def test_run_fires_again_after_cooldown_expires(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    copilot.run(conn, _cfg(), today=TODAY)
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    conn.execute("UPDATE alerts_log SET fired_at = ?", (old,))
    conn.commit()
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "sent"
    assert len(calls) == 2


def test_run_sends_only_the_non_cooled_condition_when_one_is_blocked(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _seed_loss(conn)
    _seed_gain(conn, year=2026)
    alerts.log_fired(conn, copilot.KIND_VESTING, "info", "x", "x już wysłane")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "sent"
    assert result["kinds"] == [copilot.KIND_TAX_LOSS]
    message = calls[0][0][2]
    assert "vesting" not in message.lower()


def test_zero_min_interval_disables_antispam(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    cfg = _cfg(copilot_min_interval_days=0)
    copilot.run(conn, cfg, today=TODAY)
    result = copilot.run(conn, cfg, today=TODAY)
    assert result["reason"] == "sent"
    assert len(calls) == 2


# ============================================================
# C. narracja AI #2 (opcjonalna, zawsze z deterministycznym fallbackiem)
# ============================================================

def test_run_uses_ai_narration_when_enabled(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _notify_spy(monkeypatch, True)
    tasks = _fake_narration(monkeypatch, answer="Nadchodzi vesting, sprawdź szczegóły.")
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert tasks == ["copilot_narration"]
    assert result["narrated"] is True
    assert "Nadchodzi vesting" in result["message"]


def test_run_skips_ai_when_narration_disabled(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _notify_spy(monkeypatch, True)
    _forbid_ai(monkeypatch)
    result = copilot.run(conn, _cfg(ai_chat_narration_enabled=0), today=TODAY)
    assert result["reason"] == "sent"
    assert result["narrated"] is False


def test_run_still_sends_when_narration_fails(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _notify_spy(monkeypatch, True)
    _fail_narration(monkeypatch)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "sent"
    assert result["narrated"] is False


def test_message_always_contains_engine_sentences_even_with_narration(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05", qty=12.5)
    _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch, answer="Krótkie podsumowanie AI.")
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert "Krótkie podsumowanie AI." in result["message"]
    assert "12,5" in result["message"] or "12.5" in result["message"]


# ============================================================
# D. orkiestracja run()
# ============================================================

def test_run_does_nothing_when_copilot_disabled(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    result = copilot.run(conn, _cfg(copilot_enabled=0), today=TODAY)
    assert result["reason"] == "disabled"
    assert calls == []


def test_run_does_nothing_without_notify_service(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    result = copilot.run(conn, _cfg(notify_service=""), today=TODAY)
    assert result["reason"] == "no_notify_service"
    assert calls == []


def test_run_does_not_notify_when_no_conditions(conn, monkeypatch):
    calls = _notify_spy(monkeypatch, True)
    _forbid_ai(monkeypatch)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "no_conditions"
    assert calls == []


def test_run_sends_exactly_one_combined_push_for_three_conditions(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05", qty=12.5)
    _seed_loss(conn, amount=200.0)
    _seed_gain(conn, year=2026)
    _seed_open_lot(conn)
    _seed_dividend_schedule(conn, "2026-08-30")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert len(calls) == 1
    assert result["kinds"] == [copilot.KIND_VESTING, copilot.KIND_TAX_LOSS, copilot.KIND_DIVIDEND]
    message = result["message"]
    assert "12,5" in message or "12.5" in message
    assert "200" in message


def test_run_does_not_mark_fired_when_notify_fails(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _notify_spy(monkeypatch, False)
    _fake_narration(monkeypatch)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "notify_failed"
    assert conn.execute("SELECT * FROM alerts_log").fetchall() == []


def test_run_title_names_single_condition(conn, monkeypatch):
    _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    _seed_vest(conn, "2026-09-05")
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["title"] == "Nokia — Zbliżający się vesting"


def test_run_title_counts_bundle_of_three(conn, monkeypatch):
    _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    _seed_vest(conn, "2026-09-05")
    _seed_loss(conn)
    _seed_gain(conn, year=2026)
    _seed_open_lot(conn)
    _seed_dividend_schedule(conn, "2026-08-30")
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["title"] == "Nokia — co-pilot (3 sprawy)"


def test_run_uses_slash_form_notify_service(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    copilot.run(conn, _cfg(notify_service="notify.family"), today=TODAY)
    assert calls[0][0][0] == "notify/family"


# ============================================================
# E. preview() — zero skutków ubocznych
# ============================================================

def test_preview_never_calls_ai(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    _forbid_ai(monkeypatch)
    result = copilot.preview(conn, _cfg(), today=TODAY)
    assert result["ok"] is True


def test_preview_never_sends_notification(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    copilot.preview(conn, _cfg(), today=TODAY)
    assert calls == []


def test_preview_does_not_consume_cooldown(conn, monkeypatch):
    _seed_vest(conn, "2026-09-05")
    calls = _notify_spy(monkeypatch, True)
    _fake_narration(monkeypatch)
    copilot.preview(conn, _cfg(), today=TODAY)
    result = copilot.run(conn, _cfg(), today=TODAY)
    assert result["reason"] == "sent"
    assert len(calls) == 1


def test_preview_reports_would_send_false_when_all_cooled(conn):
    _seed_vest(conn, "2026-09-05")
    alerts.log_fired(conn, copilot.KIND_VESTING, "info", "x", "x")
    result = copilot.preview(conn, _cfg(), today=TODAY)
    assert result["would_send"] is False
    assert result["conditions"][0]["allowed"] is False
    assert result["conditions"][0]["last_fired_at"] is not None


def test_preview_returns_ok_and_lines_like_other_previews(conn):
    _seed_vest(conn, "2026-09-05")
    result = copilot.preview(conn, _cfg(), today=TODAY)
    assert result["ok"] is True
    assert result["lines"][0]["label"] == "Zbliżający się vesting"
    assert isinstance(result["lines"][0]["value"], str)
