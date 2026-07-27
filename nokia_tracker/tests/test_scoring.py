"""Batchowe ocenianie newsów: tylko nieocenione trafiają do promptu, wynik
zapisuje się do news_scores, model providera zapisany per ocena."""
import pytest

from nokia_tracker.ai import provider, scoring
from nokia_tracker.ai.errors import AIProviderError


def _insert_news(conn, n=3):
    ids = []
    for i in range(n):
        cur = conn.execute(
            "INSERT INTO news (title, url_canonical, title_hash, published_at, raw_summary) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"Tytuł {i}", f"https://example.com/{i}", f"hash{i}",
             f"2026-07-2{i}T10:00:00Z", f"Streszczenie {i}"))
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _cfg():
    return {"ai_primary": "local", "ai_fallback": "gemini",
           "local_llm_base_url": "http://x/v1", "local_llm_api_key": "k",
           "local_llm_model": "m", "gemini_api_key": "", "gemini_model": "m2",
           "anthropic_api_key": "", "anthropic_model": "m3",
           "ai_max_tokens": 4000, "ai_max_calls_per_day": 40, "ai_news_batch_size": 15}


def test_score_pending_no_news_returns_zero_without_calling_ai(conn, monkeypatch):
    called = []
    monkeypatch.setattr(provider, "analyze", lambda *a, **kw: called.append(1))
    assert scoring.score_pending(conn, _cfg()) == 0
    assert called == []


def test_score_pending_writes_scores(conn, monkeypatch):
    ids = _insert_news(conn, 2)
    fake_result = {"scores": [
        {"index": 0, "sentiment": 0.5, "impact": 2, "horizon": "weeks",
         "thesis_pl": "teza 0", "price_effect_pct_est": 1.5, "tags": ["kontrakt"]},
        {"index": 1, "sentiment": -0.3, "impact": 1, "horizon": "immediate",
         "thesis_pl": "teza 1", "price_effect_pct_est": -0.5, "tags": []},
    ]}
    monkeypatch.setattr(provider, "analyze", lambda *a, **kw: fake_result)
    monkeypatch.setattr(provider, "active_provider", lambda: "local")

    scored = scoring.score_pending(conn, _cfg())
    assert scored == 2

    rows = conn.execute("SELECT news_id, sentiment, impact, model FROM news_scores").fetchall()
    assert len(rows) == 2
    assert {r["news_id"] for r in rows} == set(ids)
    assert {round(r["sentiment"], 1) for r in rows} == {0.5, -0.3}
    assert all(r["model"] == "local" for r in rows)


def test_score_pending_excludes_already_scored(conn, monkeypatch):
    ids = _insert_news(conn, 2)
    conn.execute("INSERT INTO news_scores (news_id, sentiment, impact, horizon, thesis_pl, "
                "tags, model) VALUES (?, 0.1, 1, 'weeks', 't', '[]', 'local')", (ids[0],))
    conn.commit()

    captured = {}

    def fake_analyze(conn_, cfg, task, prompt, schema, max_tokens):
        captured["prompt"] = prompt
        return {"scores": [{"index": 0, "sentiment": 0.0, "impact": 0, "horizon": "weeks",
                           "thesis_pl": "t", "tags": []}]}

    monkeypatch.setattr(provider, "analyze", fake_analyze)
    monkeypatch.setattr(provider, "active_provider", lambda: "local")

    scored = scoring.score_pending(conn, _cfg())
    assert scored == 1
    assert "Tytuł 1" in captured["prompt"]
    assert "Tytuł 0" not in captured["prompt"]  # już oceniony -> poza batchem


def test_score_pending_ai_chain_failure_returns_zero(conn, monkeypatch):
    _insert_news(conn, 1)
    monkeypatch.setattr(provider, "analyze",
                        lambda *a, **kw: (_ for _ in ()).throw(AIProviderError("chain down")))
    assert scoring.score_pending(conn, _cfg()) == 0
    assert conn.execute("SELECT COUNT(*) c FROM news_scores").fetchone()["c"] == 0


def test_score_pending_ignores_out_of_range_index(conn, monkeypatch):
    _insert_news(conn, 1)
    monkeypatch.setattr(provider, "analyze", lambda *a, **kw: {"scores": [
        {"index": 5, "sentiment": 0, "impact": 0, "horizon": "weeks", "thesis_pl": "t", "tags": []}
    ]})
    monkeypatch.setattr(provider, "active_provider", lambda: "local")
    assert scoring.score_pending(conn, _cfg()) == 0
