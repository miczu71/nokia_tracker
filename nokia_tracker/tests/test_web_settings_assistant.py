"""Trasy /settings, /analyze-now, /asystent (+API JSON) i
/api/preview/copilot — wszystkie żyją dziś w `web/routes_ai.py` i
`web/routes_ustawienia.py`. Wydzielone z `test_web.py`
(E3 — docs/ROADMAP_V3.md); fixture `client` w conftest.py.

Reguła M (docs/ROADMAP_V3.md): `analysis`/`ai_chat` są importowane jako
MODUŁY w `web/routes_ai.py` i wołane przez atrybut — testy tutaj łatają je
przez `monkeypatch.setattr(analysis, "run_daily_analysis", ...)` /
`monkeypatch.setattr(ai_chat, "ask", ...)`, co działa wyłącznie przy takim
późnym wiązaniu."""
from nokia_tracker import analysis, db as dbm
from nokia_tracker.web import create_app


# --- settings ---

def test_settings_post_updates_and_redirects(client):
    resp = client.post("/settings", data={
        "ai_primary": "gemini", "ai_fallback": "anthropic",
        "local_llm_model": "custom-model", "gemini_model": "gemini-x",
        "anthropic_model": "claude-x",
        "alert_sentiment_drop": "0.7", "alert_price_move_pct": "5.0",
        "alert_min_interval_minutes": "60", "notify_service": "notify.family",
        "cost_basis_policy": "own_plus_drip",
    })
    assert resp.status_code == 302

    resp2 = client.get("/settings")
    html = resp2.get_data(as_text=True)
    assert 'value="notify.family"' in html
    assert 'selected' in html  # przynajmniej jeden select ma zapisaną wartość


def test_settings_checkbox_unchecked_when_omitted(client):
    # checkboxy HTML nie wysyłają nic, gdy odznaczone -> ustawienie=0
    client.post("/settings", data={
        "ai_primary": "local", "ai_fallback": "gemini",
        "local_llm_model": "m", "gemini_model": "m2", "anthropic_model": "m3",
        "alert_sentiment_drop": "0.5", "alert_price_move_pct": "3.0",
        "alert_min_interval_minutes": "120", "notify_service": "",
        "cost_basis_policy": "own_only",
        # brak ai_recommendations_enabled i alert_on_forecast_break
    })
    resp = client.get("/settings")
    html = resp.get_data(as_text=True)
    # brak "checked" przy tych dwóch polach
    assert html.count("checked") == 0


# --- analyze-now ---

def test_analyze_now_calls_analysis_and_redirects(client, monkeypatch):
    calls = []
    monkeypatch.setattr(analysis, "run_daily_analysis",
                        lambda *a, **kw: (calls.append(a), True)[1])
    resp = client.post("/analyze-now")
    assert resp.status_code == 302
    assert "analyzed=1" in resp.headers["Location"]
    assert len(calls) == 1


def test_analyze_now_failure_still_redirects(client, monkeypatch):
    monkeypatch.setattr(analysis, "run_daily_analysis", lambda *a, **kw: False)
    resp = client.post("/analyze-now")
    assert resp.status_code == 302
    assert "analyzed=0" in resp.headers["Location"]


def test_settings_post_saves_other_net_worth_and_threshold(client):
    resp = client.post("/settings", data={
        "ai_primary": "local", "ai_fallback": "gemini",
        "other_net_worth_pln": "150000.5", "concentration_alert_pct": "30.0",
    })
    assert resp.status_code == 302
    html = client.get("/settings").get_data(as_text=True)
    assert 'value="150000.5"' in html
    assert 'value="30.0"' in html


# --- /asystent (krok 29): zero żywego AI, ai_chat.ask() mockowane ---

def test_assistant_get_shows_empty_history(client):
    resp = client.get("/asystent")
    assert resp.status_code == 200
    assert "Asystent" in resp.get_data(as_text=True)


def test_assistant_post_calls_ask_and_redirects_without_question_in_url(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: (calls.append(q), {
        "ok": True, "intent": "ile_moge_sprzedac", "params": {}, "title": "Ile mogę sprzedać",
        "lines": [], "detail_url": "/plan", "error": None, "answer_pl": "Możesz sprzedać 0 akcji.",
    })[1])
    resp = client.post("/asystent", data={"question": "Ile mogę sprzedać?"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/asystent"  # bez ?question= — odświeżenie nie powtarza pytania
    assert calls == ["Ile mogę sprzedać?"]


def test_assistant_post_with_empty_question_does_not_call_ask(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    resp = client.post("/asystent", data={"question": "   "})
    assert resp.status_code == 302
    assert calls == []


def test_assistant_get_with_q_param_asks_then_redirects_to_plain_url(client, monkeypatch):
    # ?q= (pole szybkiego pytania na pulpicie, krok 29.7) MUSI przekierować do
    # czystego /asystent po odpowiedzi — inaczej odświeżenie strony powtarzałoby
    # zapytanie AI (ten sam powód co POST-redirect-GET dla formularza).
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: (calls.append(q), {
        "ok": True, "intent": "inne", "params": {}, "title": "x", "lines": [],
        "detail_url": None, "error": None, "answer_pl": "x",
    })[1])
    resp = client.get("/asystent?q=Ile+zarobi%C5%82em%3F")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/asystent"
    assert calls == ["Ile zarobiłem?"]


def test_assistant_get_without_q_does_not_call_ask(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    client.get("/asystent")
    assert calls == []


def _insert_chat_log_row(tmp_path, db_name, **overrides):
    db_path = str(tmp_path / db_name)
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    row = {
        "question": "Ile mogę sprzedać?", "intent": "ile_moge_sprzedac",
        "params_json": "{}", "result_json": "{}",
        "answer_pl": "Możesz sprzedać 0 akcji bez ograniczeń.",
        "provider": "local", "ok": 1, "error": None,
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO chat_log (question, intent, params_json, result_json, answer_pl, "
        "provider, ok, error) VALUES (:question, :intent, :params_json, :result_json, "
        ":answer_pl, :provider, :ok, :error)", row)
    conn.commit()
    conn.close()
    return db_path


def test_assistant_get_renders_history(tmp_path):
    db_path = _insert_chat_log_row(tmp_path, "history.db")
    app = create_app(db_path)
    with app.test_client() as c:
        html = c.get("/asystent").get_data(as_text=True)
    assert "Ile mogę sprzedać?" in html
    assert "Możesz sprzedać 0 akcji bez ograniczeń." in html


def test_assistant_disabled_skips_ask_and_shows_message(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    client.post("/settings", data={})  # brak pola = odznaczony checkbox = wyłączony
    resp = client.post("/asystent", data={"question": "Ile zarobiłem?"})
    assert resp.status_code == 302
    assert calls == []
    html = client.get("/asystent").get_data(as_text=True)
    assert "wyłączony" in html.lower()


def test_assistant_answer_text_is_escaped_not_raw_html(tmp_path):
    db_path = _insert_chat_log_row(
        tmp_path, "xss.db", question="test", intent="inne",
        answer_pl="<script>alert(1)</script>")
    app = create_app(db_path)
    with app.test_client() as c:
        html = c.get("/asystent").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_api_assistant_get_returns_json(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: {
        "ok": True, "intent": "ile_moge_sprzedac", "params": {}, "title": "Ile mogę sprzedać",
        "lines": [{"label": "Wolne", "value": 10, "unit": "szt."}], "detail_url": "/plan",
        "error": None, "answer_pl": "Możesz sprzedać 10 akcji.",
    })
    resp = client.get("/api/asystent?q=Ile+mog%C4%99+sprzeda%C4%87%3F")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["answer_pl"] == "Możesz sprzedać 10 akcji."


def test_api_assistant_post_accepts_json_body(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: (calls.append(q), {
        "ok": True, "intent": "inne", "params": {}, "title": "x", "lines": [],
        "detail_url": None, "error": None, "answer_pl": "x",
    })[1])
    resp = client.post("/api/asystent", json={"question": "Kiedy mam vesting?"})
    assert resp.status_code == 200
    assert calls == ["Kiedy mam vesting?"]


def test_api_assistant_disabled_returns_ok_false_without_calling_ask(client, monkeypatch):
    from nokia_tracker.ai import chat as ai_chat
    calls = []
    monkeypatch.setattr(ai_chat, "ask", lambda conn, cfg, q: calls.append(q))
    client.post("/settings", data={})
    resp = client.get("/api/asystent?q=test")
    data = resp.get_json()
    assert data["ok"] is False
    assert calls == []


def test_assistant_page_shows_ai_status_bar(client):
    html = client.get("/asystent").get_data(as_text=True)
    assert "local (freellmapi)" in html


# --- /api/preview/copilot (krok 33) — zero skutków ubocznych, patrz
# ai/copilot.py::preview() i tests/test_ai_copilot.py dla logiki warunków ---

def test_preview_copilot_returns_ok_shape(client):
    resp = client.get("/api/preview/copilot")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["would_send"] is False  # pusta baza -> brak warunków
    assert data["conditions"] == []
    assert data["lines"] == []


def test_preview_copilot_rejects_malformed_today_param(client):
    resp = client.get("/api/preview/copilot?today=nie-data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "error" in data
