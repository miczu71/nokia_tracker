"""Push o nowych newsach + dzienny digest AI (krok 22). Zero żywego
HA/notify — ha_client.notify monkeypatchowane, wzorzec z test_alerts.py."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from nokia_tracker import ha_client, notifier


def _cfg(**overrides):
    base = {"notify_service": "notify.mobile_app_op12", "notify_news_min_impact": 1}
    base.update(overrides)
    return base


def _insert_news(conn, news_id_hint, *, impact=2, sentiment=0.4, published_at=None,
                 title="Nokia ogłasza nowy kontrakt", url=None, thesis_pl="Pozytywny sygnał."):
    published_at = published_at or datetime.now(timezone.utc).isoformat()
    url = url or f"https://example.com/{news_id_hint}"
    cur = conn.execute(
        "INSERT INTO news (title, url_canonical, title_hash, published_at) "
        "VALUES (?, ?, ?, ?)",
        (title, url, f"hash-{news_id_hint}", published_at))
    news_id = cur.lastrowid
    conn.execute(
        "INSERT INTO news_scores (news_id, sentiment, impact, horizon, thesis_pl, "
        "tags, model) VALUES (?, ?, ?, 'weeks', ?, '[]', 'test')",
        (news_id, sentiment, impact, thesis_pl))
    conn.commit()
    return news_id


# --- notify_new_news: filtrowanie i dedup ---

def test_zero_impact_news_not_sent_but_marked_skipped(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: calls.append(a) or True)
    _insert_news(conn, "a", impact=0)
    sent = notifier.notify_new_news(conn, _cfg())
    assert sent == 0
    assert calls == []
    row = conn.execute("SELECT notified_at FROM news").fetchone()
    assert row["notified_at"] == "skipped"


def test_news_not_sent_twice(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: calls.append(a) or True)
    _insert_news(conn, "a", impact=2)
    first = notifier.notify_new_news(conn, _cfg())
    second = notifier.notify_new_news(conn, _cfg())
    assert first == 1
    assert second == 0
    assert len(calls) == 1


def test_failed_notify_leaves_notified_at_null_for_retry(conn, monkeypatch):
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: False)
    _insert_news(conn, "a", impact=2)
    sent = notifier.notify_new_news(conn, _cfg())
    assert sent == 0
    row = conn.execute("SELECT notified_at FROM news").fetchone()
    assert row["notified_at"] is None  # nie 'skipped' -> spróbuje ponownie


def test_stale_failed_notify_eventually_marked_skipped(conn, monkeypatch):
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: False)
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    _insert_news(conn, "a", impact=2, published_at=old)
    notifier.notify_new_news(conn, _cfg())
    row = conn.execute("SELECT notified_at FROM news").fetchone()
    assert row["notified_at"] == "skipped"  # starsze niż 2 dni -> nie próbuj już wysyłać


def test_max_per_run_truncates_batch(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: calls.append(a) or True)
    for i in range(notifier._MAX_PER_RUN + 3):
        _insert_news(conn, f"n{i}", impact=2)
    sent = notifier.notify_new_news(conn, _cfg())
    assert sent == notifier._MAX_PER_RUN


def test_empty_notify_service_sends_nothing(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: calls.append(a) or True)
    _insert_news(conn, "a", impact=2)
    sent = notifier.notify_new_news(conn, _cfg(notify_service=""))
    assert sent == 0
    assert calls == []


# --- notify_new_news: treść powiadomienia ---

def test_notification_payload_includes_url_and_content(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify",
                        lambda service, title, message, data=None: calls.append(
                            (service, title, message, data)) or True)
    _insert_news(conn, "a", impact=3, sentiment=-0.6,
                url="https://example.com/big-news", thesis_pl="Ryzyko dla wyników.")
    notifier.notify_new_news(conn, _cfg())
    service, title, message, data = calls[0]
    assert service == "notify/mobile_app_op12"
    assert "Nokia" in title
    assert "Ryzyko dla wyników." in message
    assert "wysoki" in message  # impact=3 -> etykieta "wysoki"
    assert data["url"] == "https://example.com/big-news"
    assert data["clickAction"] == "https://example.com/big-news"
    assert data["group"] == "nokia-news"


# --- send_daily_digest ---

def test_digest_without_briefing_sends_abbreviated_content(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify",
                        lambda service, title, message, data=None: calls.append(
                            (service, title, message, data)) or True)
    values = {"price_eur": 4.2, "change_pct_day": 1.5, "change_abs_day": 0.06}
    ok = notifier.send_daily_digest(conn, _cfg(), values)
    assert ok is True
    assert len(calls) == 1
    _, title, message, data = calls[0]
    assert "4.200 EUR" in message
    assert data["tag"] == "nokia-digest"


def test_digest_with_briefing_includes_recommendation_and_risks(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify",
                        lambda service, title, message, data=None: calls.append(
                            (service, title, message, data)) or True)
    conn.execute(
        "INSERT INTO briefings (generated_at, text, tts_text, sentiment_avg, news_count, "
        "verdict, key_risks, recommendation, recommendation_reason_pl, "
        "recommendation_confidence, model) VALUES (?, 'Briefing dnia.', 'tts', 0.3, 2, "
        "'mieszane', ?, 'akumuluj', 'Fundamenty stabilne.', 0.7, 'test')",
        (datetime.now(timezone.utc).isoformat(), json.dumps(["Ryzyko A", "Ryzyko B"])))
    conn.commit()
    ok = notifier.send_daily_digest(conn, _cfg(), {"price_eur": 4.2})
    assert ok is True
    message = calls[0][2]
    assert "Briefing dnia." in message
    assert "akumuluj" in message
    assert "Ryzyko A" in message


def test_digest_empty_notify_service_does_not_send(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify", lambda *a, **k: calls.append(a) or True)
    ok = notifier.send_daily_digest(conn, _cfg(notify_service=""), {"price_eur": 4.2})
    assert ok is False
    assert calls == []
