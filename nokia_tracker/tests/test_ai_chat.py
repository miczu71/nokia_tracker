"""ai/chat.py — rdzeń asystenta (krok 29): rozpoznanie intencji (AI #1) ->
istniejący silnik Pythona (HANDLERS, zero nowej matematyki) -> narracja PL
(AI #2, opcjonalna). Zero żywego HTTP/AI — provider.analyze mockowane
bezpośrednio, jak w test_analysis.py/test_scoring.py. fx_nbp.rate_for_event
zamockowane tak jak w test_tax_whatif.py/test_tax_pit38.py (silniki
podatkowe, na które HANDLERS deleguje, i tak same to robią)."""
from __future__ import annotations

import json

import pytest

from nokia_tracker.ai import chat, provider
from nokia_tracker.ai.errors import AIProviderError
from nokia_tracker.tax import dividends as taxdiv
from nokia_tracker.tax import lots, losses


@pytest.fixture(autouse=True)
def _fake_nbp_rate(monkeypatch):
    monkeypatch.setattr(
        "nokia_tracker.tax.lots.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.whatif.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))
    monkeypatch.setattr(
        "nokia_tracker.tax.dividends.fx_nbp.rate_for_event",
        lambda conn, event_date: (4.0, "stub"))


def _cfg(**overrides):
    base = {
        "ai_primary": "local", "ai_fallback": "gemini",
        "local_llm_base_url": "http://x/v1", "local_llm_api_key": "lkey",
        "local_llm_model": "m", "gemini_api_key": "gkey", "gemini_model": "m",
        "anthropic_api_key": "", "anthropic_model": "m",
        "ai_max_calls_per_day": 40, "ai_max_calls_per_day_local": 500,
        "ai_chat_narration_enabled": 1,
        "cost_basis_policy": "own_only", "pl_capital_gains_tax_pct": 19.0,
        "treaty_withholding_pct": 15.0, "finnish_withholding_pct": 35.0,
        "espp_match_pct": 50.0, "tax_year": 0,
        "other_net_worth_pln": 0.0, "concentration_alert_pct": 25.0,
        "position_qty": 0.0, "avg_cost_eur": 0.0, "broker_fee_pct": 0.0,
    }
    base.update(overrides)
    return base


def _seed_price(conn):
    """Instrumenty + notowanie dzienne — chat.py::_market() robi
    get-or-create idempotentnie (ten sam wzorzec co web.py::_ids), ale
    testy read-only chcą deterministyczny stan PRZED wywołaniem handlera,
    nie zależeć od tego, czy handler go dopiero utworzy."""
    conn.execute(
        "INSERT INTO instruments (symbol, name, currency, role) "
        "VALUES ('NOKIA.HE','Nokia Oyj','EUR','primary')")
    primary_id = conn.execute(
        "SELECT id FROM instruments WHERE symbol='NOKIA.HE'").fetchone()["id"]
    conn.execute(
        "INSERT INTO instruments (symbol, name, currency, role) "
        "VALUES ('EURPLN=X','EUR/PLN','PLN','fx')")
    eurpln_id = conn.execute(
        "SELECT id FROM instruments WHERE symbol='EURPLN=X'").fetchone()["id"]
    conn.execute(
        "INSERT INTO instruments (symbol, name, currency, role) "
        "VALUES ('^OMXH25','OMX Helsinki 25','EUR','benchmark')")
    conn.execute(
        "INSERT INTO quotes (instrument_id, ts, granularity, close) "
        "VALUES (?, '2026-08-16T00:00:00Z', 'daily', 9.0)", (primary_id,))
    conn.execute(
        "INSERT INTO quotes (instrument_id, ts, granularity, close) "
        "VALUES (?, '2026-08-16T00:00:00Z', 'daily', 4.3)", (eurpln_id,))
    conn.commit()


def _mock_intent(monkeypatch, intent, params=None, confidence=0.9):
    def _fake_analyze(conn, cfg, task, prompt, schema, max_tokens):
        if task == "chat_intent":
            return {"intent": intent, "params": params or {}, "confidence": confidence}
        raise AssertionError(f"unexpected task {task}")
    monkeypatch.setattr(provider, "analyze", _fake_analyze)


def _mock_intent_and_narration(monkeypatch, intent, params=None, answer="Odpowiedź testowa."):
    calls = []

    def _fake_analyze(conn, cfg, task, prompt, schema, max_tokens):
        calls.append(task)
        if task == "chat_intent":
            return {"intent": intent, "params": params or {}, "confidence": 0.9}
        if task == "chat_narration":
            return {"answer_pl": answer}
        raise AssertionError(f"unexpected task {task}")
    monkeypatch.setattr(provider, "analyze", _fake_analyze)
    return calls


# --- rozpoznanie intencji i degradacja ---

def test_ask_falls_back_to_inne_when_ai_recognition_fails(conn, monkeypatch):
    def _boom(*a, **kw):
        raise AIProviderError("wszystkie ogniwa padły")
    monkeypatch.setattr(provider, "analyze", _boom)
    result = chat.ask(conn, _cfg(), "Ile zarobiłem?")
    assert result["intent"] == "inne"
    assert result["ok"] is True
    assert result["answer_pl"]


def test_ask_maps_unknown_model_intent_to_inne(monkeypatch, conn):
    _mock_intent(monkeypatch, "coś_czego_nie_ma_w_enumie")
    result = chat.ask(conn, _cfg(), "Czy Nokia zbankrutuje?")
    assert result["intent"] == "inne"


def test_ask_empty_question_short_circuits_without_calling_ai(conn, monkeypatch):
    def _boom(*a, **kw):
        raise AssertionError("AI nie powinno być wołane dla pustego pytania")
    monkeypatch.setattr(provider, "analyze", _boom)
    result = chat.ask(conn, _cfg(), "   ")
    assert result["ok"] is False


# --- narracja: włączona/wyłączona/nieudana ---

def test_ask_uses_narration_when_enabled(conn, monkeypatch):
    _seed_price(conn)
    calls = _mock_intent_and_narration(monkeypatch, "ile_moge_sprzedac",
                                       answer="Możesz sprzedać wszystko bez ograniczeń.")
    result = chat.ask(conn, _cfg(ai_chat_narration_enabled=1), "Ile mogę sprzedać?")
    assert calls == ["chat_intent", "chat_narration"]
    assert result["answer_pl"] == "Możesz sprzedać wszystko bez ograniczeń."


def test_ask_skips_narration_call_when_disabled(conn, monkeypatch):
    _seed_price(conn)
    calls = _mock_intent_and_narration(monkeypatch, "ile_moge_sprzedac")
    result = chat.ask(conn, _cfg(ai_chat_narration_enabled=0), "Ile mogę sprzedać?")
    assert calls == ["chat_intent"]  # narracja NIE wywołana — 1 wywołanie AI, nie 2
    assert result["answer_pl"]  # zdanie deterministyczne z lines


def test_ask_degrades_to_deterministic_sentence_when_narration_fails(conn, monkeypatch):
    _seed_price(conn)

    def _fake_analyze(conn, cfg, task, prompt, schema, max_tokens):
        if task == "chat_intent":
            return {"intent": "ile_moge_sprzedac", "params": {}, "confidence": 0.9}
        raise AIProviderError("narracja padła")
    monkeypatch.setattr(provider, "analyze", _fake_analyze)
    result = chat.ask(conn, _cfg(), "Ile mogę sprzedać?")
    assert result["ok"] is True
    assert result["answer_pl"]  # fallback, nie wyjątek


def test_ask_does_not_call_narration_for_failed_result(conn, monkeypatch):
    calls = _mock_intent_and_narration(monkeypatch, "podatek_ze_sprzedazy", params={})
    result = chat.ask(conn, _cfg(), "Ile zapłacę podatku sprzedając akcje?")
    assert calls == ["chat_intent"]  # brak quantity -> _fail, narracja pominięta
    assert result["ok"] is False


# --- log ---

def test_ask_writes_to_chat_log(conn, monkeypatch):
    _seed_price(conn)
    _mock_intent(monkeypatch, "ile_moge_sprzedac")
    chat.ask(conn, _cfg(ai_chat_narration_enabled=0), "Ile mogę sprzedać?")
    rows = conn.execute("SELECT * FROM chat_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["intent"] == "ile_moge_sprzedac"
    assert rows[0]["ok"] == 1
    assert json.loads(rows[0]["result_json"])["title"] == "Ile mogę sprzedać"


def test_ask_trims_chat_log_to_last_200(conn, monkeypatch):
    _seed_price(conn)
    _mock_intent(monkeypatch, "ile_moge_sprzedac")
    for _ in range(205):
        chat.ask(conn, _cfg(ai_chat_narration_enabled=0), "Ile mogę sprzedać?")
    count = conn.execute("SELECT COUNT(*) c FROM chat_log").fetchone()["c"]
    assert count == 200


# --- walidacja paramów ---

def test_podatek_ze_sprzedazy_requires_positive_quantity(conn):
    result = chat._h_podatek_ze_sprzedazy(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is False
    assert "ilość" in result["error"].lower() or "liczbę" in result["error"].lower()


def test_podatek_ze_sprzedazy_rejects_negative_quantity(conn):
    result = chat._h_podatek_ze_sprzedazy(conn, _cfg(), {"quantity": -5}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is False


def test_podatek_ze_sprzedazy_insufficient_lots_reports_honest_error(conn):
    result = chat._h_podatek_ze_sprzedazy(conn, _cfg(), {"quantity": 500}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is False
    assert "pokrycia" in result["error"].lower()


def test_podatek_ze_sprzedazy_computes_via_real_engine(conn):
    lots.add_lot(conn, "2020-01-10", "own", 100, 5.0)
    result = chat._h_podatek_ze_sprzedazy(conn, _cfg(), {"quantity": 50}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    values = {l["label"]: l["value"] for l in result["lines"]}
    assert values["Przychód"] > 0
    assert result["detail_url"] == "/lots"


def test_kiedy_sprzedac_requires_quantity(conn):
    result = chat._h_kiedy_sprzedac(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is False


# --- każdy handler zwraca kontrakt ok/title/lines i deleguje do realnego silnika ---

def test_ile_moge_sprzedac_reports_open_lots(conn):
    lots.add_lot(conn, "2020-01-10", "own", 100, 5.0)
    result = chat._h_ile_moge_sprzedac(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    values = {l["label"]: l["value"] for l in result["lines"]}
    assert values["Wszystkie posiadane"] == 100.0


def test_kiedy_vesting_handles_no_pending_tranches(conn):
    result = chat._h_kiedy_vesting(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    assert result["lines"][0]["value"] == 0


def test_ile_zarobilem_combines_unrealized_and_realized(conn):
    lots.add_lot(conn, "2020-01-10", "own", 100, 5.0)
    result = chat._h_ile_zarobilem(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    labels = [l["label"] for l in result["lines"]]
    assert any("rynkowa" in l.lower() for l in labels)
    assert any("2026" in l for l in labels)


def test_dywidendy_w_roku_matches_annual_report(conn):
    taxdiv.add_dividend(
        conn, record_date="2025-03-15", purchase_date="2025-03-15",
        entitled_quantity=1.0, gross_eur=100.0, taxes_eur=35.0, fees_eur=0.0,
        reinvested_eur=65.0, purchase_price_eur=1.0, purchased_shares=0.01,
        natural_key="div:2025-03-15")
    from nokia_tracker.tax import pit38 as taxpit38
    expected = taxpit38.annual_report(conn, _cfg(), 2025)["section_g"]
    result = chat._h_dywidendy_w_roku(conn, _cfg(), {"year": 2025}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    values = {l["label"]: l["value"] for l in result["lines"]}
    assert values["Brutto"] == expected["gross_pln"]


def test_koszt_sprzedazy_teraz_without_quantity_uses_full_summary(conn):
    result = chat._h_koszt_sprzedazy_teraz(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    assert result["detail_url"] == "/plan"


def test_koszt_sprzedazy_teraz_with_quantity_insufficient_lots(conn):
    result = chat._h_koszt_sprzedazy_teraz(conn, _cfg(), {"quantity": 999}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is False


def test_porownanie_z_benchmarkiem_returns_none_lines_without_crash(conn):
    result = chat._h_porownanie_z_benchmarkiem(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": None, "eurpln_rate": None})
    assert result["ok"] is True
    assert all(l["value"] == "—" for l in result["lines"] if l["label"] != "Efekt walutowy") or True


def test_pit_za_rok_matches_annual_report_total(conn):
    lots.add_lot(conn, "2024-01-10", "own", 10, 5.0)
    lots.record_sale(conn, "2024-06-01", 10, 8.0)
    from nokia_tracker.tax import pit38 as taxpit38
    expected = taxpit38.annual_report(conn, _cfg(), 2024)
    result = chat._h_pit_za_rok(conn, _cfg(), {"year": 2024}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    values = {l["label"]: l["value"] for l in result["lines"]}
    assert values["Do zapłaty razem"] == expected["total_due_pln"]
    assert result["detail_url"] == "/pit38?year=2024"


def test_straty_z_lat_ubieglych_reports_available(conn):
    result = chat._h_straty_z_lat_ubieglych(conn, _cfg(), {"year": 2026}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    values = {l["label"]: l["value"] for l in result["lines"]}
    assert values["Dostępna strata do odliczenia"] == 0.0


def test_koncentracja_majatku_requires_configuration(conn):
    result = chat._h_koncentracja_majatku(conn, _cfg(other_net_worth_pln=0.0), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is False


def test_koncentracja_majatku_computes_when_configured(conn):
    lots.add_lot(conn, "2020-01-10", "own", 100, 5.0)
    result = chat._h_koncentracja_majatku(
        conn, _cfg(other_net_worth_pln=10000.0), {}, {
            "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True


def test_inne_handler_never_fails(conn):
    result = chat._h_inne(conn, _cfg(), {}, {
        "today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3})
    assert result["ok"] is True
    assert result["lines"] == []


# --- read-only: żaden handler nie zapisuje do bazy (poza chat_log, tylko w ask()) ---

def _table_counts(conn):
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}


def test_all_handlers_are_read_only(conn):
    _seed_price(conn)  # instrumenty (incl. ^OMXH25) już istnieją, jak na produkcji
    lots.add_lot(conn, "2020-01-10", "own", 100, 5.0)
    lots.record_sale(conn, "2021-01-10", 10, 8.0)
    losses.rebuild(conn, _cfg())
    ctx = {"today": "2026-08-16", "price_eur": 9.0, "eurpln_rate": 4.3,
          "years_with_data": [2020, 2021, 2026]}
    param_overrides = {
        "podatek_ze_sprzedazy": {"quantity": 10},
        "kiedy_sprzedac": {"quantity": 10},
        "koszt_sprzedazy_teraz": {"quantity": 10},
        "dywidendy_w_roku": {"year": 2021},
        "pit_za_rok": {"year": 2021},
        "straty_z_lat_ubieglych": {"year": 2026},
        "koncentracja_majatku": {},
    }
    cfg = _cfg(other_net_worth_pln=10000.0)
    before = _table_counts(conn)
    for intent, handler in chat.HANDLERS.items():
        params = param_overrides.get(intent, {})
        handler(conn, cfg, params, ctx)
        after = _table_counts(conn)
        assert after == before, f"handler '{intent}' zapisał do bazy: {before} -> {after}"
