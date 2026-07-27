"""Smart alerty: progi, histereza (sentiment_drop względem poprzedniego
odczytu w pamięci), anty-spam (alert_min_interval_minutes) — BLUEPRINT §2,
krok 8. Zero żywego MQTT/notify — atrapa mqtt_pub, ha_client.notify
monkeypatchowane."""
from datetime import datetime, timedelta, timezone

import pytest

from nokia_tracker import alerts, ha_client


class _FakeMqtt:
    def __init__(self):
        self.published = []

    def publish_alert(self, alert):
        self.published.append(alert)


def _cfg(**overrides):
    base = {
        "alert_sentiment_drop": 0.5, "alert_price_move_pct": 3.0,
        "alert_on_forecast_break": True, "alert_min_interval_minutes": 120,
        "notify_service": "",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_last_sentiment():
    alerts._last_sentiment[0] = None
    yield
    alerts._last_sentiment[0] = None


# --- sentiment_drop: histereza (potrzebuje poprzedniego odczytu) ---

def test_sentiment_drop_first_reading_never_fires(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"sentiment_score": -0.8}, mqtt)
    assert fired == []  # brak poprzedniego odczytu -> nic do porównania


def test_sentiment_drop_fires_on_sufficient_drop(conn):
    mqtt = _FakeMqtt()
    alerts.check_and_fire(conn, _cfg(), {"sentiment_score": 0.3}, mqtt)
    fired = alerts.check_and_fire(conn, _cfg(), {"sentiment_score": -0.4}, mqtt)  # spadek 0.7
    assert len(fired) == 1
    assert fired[0]["kind"] == "sentiment_drop"
    assert fired[0]["sentiment_before"] == 0.3
    assert fired[0]["sentiment_after"] == -0.4


def test_sentiment_drop_below_threshold_does_not_fire(conn):
    mqtt = _FakeMqtt()
    alerts.check_and_fire(conn, _cfg(), {"sentiment_score": 0.3}, mqtt)
    fired = alerts.check_and_fire(conn, _cfg(), {"sentiment_score": 0.1}, mqtt)  # spadek 0.2 < 0.5
    assert fired == []


def test_sentiment_rise_does_not_fire(conn):
    mqtt = _FakeMqtt()
    alerts.check_and_fire(conn, _cfg(), {"sentiment_score": -0.5}, mqtt)
    fired = alerts.check_and_fire(conn, _cfg(), {"sentiment_score": 0.5}, mqtt)  # wzrost
    assert fired == []


def test_sentiment_drop_oscillation_around_threshold_only_fires_once_per_interval(conn):
    """Kluczowy test histerezy: wartość oscylująca tuż nad/pod progiem co
    chwilę nie zalewa alertami — anty-spam (alert_min_interval_minutes)
    ogranicza do jednego alertu na okres, niezależnie ile razy próg
    zostanie przekroczony w tym oknie."""
    mqtt = _FakeMqtt()
    cfg = _cfg(alert_min_interval_minutes=120)
    readings = [0.5, -0.1, 0.4, -0.2, 0.5, -0.15]  # naprzemienne przekroczenia progu 0.5
    all_fired = []
    for r in readings:
        all_fired.extend(alerts.check_and_fire(conn, cfg, {"sentiment_score": r}, mqtt))
    assert len(all_fired) == 1  # tylko pierwsze przekroczenie odpaliło alert
    assert len(conn.execute("SELECT * FROM alerts_log").fetchall()) == 1


def test_sentiment_drop_fires_again_after_interval_elapses(conn):
    mqtt = _FakeMqtt()
    cfg = _cfg(alert_min_interval_minutes=120)
    alerts.check_and_fire(conn, cfg, {"sentiment_score": 0.5}, mqtt)
    alerts.check_and_fire(conn, cfg, {"sentiment_score": -0.5}, mqtt)  # 1. alert
    # cofamy fired_at "sztucznie" o więcej niż min_interval, symulując upływ czasu
    old = (datetime.now(timezone.utc) - timedelta(minutes=130)).isoformat()
    conn.execute("UPDATE alerts_log SET fired_at = ?", (old,))
    conn.commit()
    alerts.check_and_fire(conn, cfg, {"sentiment_score": 0.4}, mqtt)
    fired = alerts.check_and_fire(conn, cfg, {"sentiment_score": -0.6}, mqtt)  # 2. alert
    assert len(fired) == 1
    assert len(conn.execute("SELECT * FROM alerts_log").fetchall()) == 2


# --- price_move_pct ---

def test_price_move_fires_above_threshold(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"change_pct_day": -4.5}, mqtt)
    assert fired[0]["kind"] == "price_move_pct"
    assert "spadł" in fired[0]["title"]


def test_price_move_below_threshold_does_not_fire(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"change_pct_day": 1.0}, mqtt)
    assert fired == []


def test_price_move_none_does_not_fire(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"change_pct_day": None}, mqtt)
    assert fired == []


# --- price_breaks_forecast ---

def test_forecast_break_fires_when_price_outside_ci(conn):
    conn.execute(
        "INSERT INTO forecasts (horizon, created_at, target_date, price_at_creation, "
        "predicted_price, ci_low, ci_high, confidence, model) VALUES "
        "('1w', '2026-07-27T10:00:00+00:00', '2026-08-03', 9.0, 9.2, 8.8, 9.6, 0.6, 'local')")
    conn.commit()
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"price_eur": 10.0}, mqtt)
    assert fired[0]["kind"] == "price_breaks_forecast"


def test_forecast_break_does_not_fire_when_within_ci(conn):
    conn.execute(
        "INSERT INTO forecasts (horizon, created_at, target_date, price_at_creation, "
        "predicted_price, ci_low, ci_high, confidence, model) VALUES "
        "('1w', '2026-07-27T10:00:00+00:00', '2026-08-03', 9.0, 9.2, 8.8, 9.6, 0.6, 'local')")
    conn.commit()
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"price_eur": 9.3}, mqtt)
    assert fired == []


def test_forecast_break_disabled_by_setting(conn):
    conn.execute(
        "INSERT INTO forecasts (horizon, created_at, target_date, price_at_creation, "
        "predicted_price, ci_low, ci_high, confidence, model) VALUES "
        "('1w', '2026-07-27T10:00:00+00:00', '2026-08-03', 9.0, 9.2, 8.8, 9.6, 0.6, 'local')")
    conn.commit()
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(alert_on_forecast_break=False),
                                  {"price_eur": 10.0}, mqtt)
    assert fired == []


def test_forecast_break_no_forecast_yet_does_not_fire(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"price_eur": 100.0}, mqtt)
    assert fired == []


# --- divergence ---

def test_divergence_fires_above_threshold(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"rel_perf_1d_vs_omxh25": 6.0}, mqtt)
    assert fired[0]["kind"] == "divergence"


def test_divergence_below_threshold_does_not_fire(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {"rel_perf_1d_vs_omxh25": 1.0}, mqtt)
    assert fired == []


# --- high_impact_news ---

def test_high_impact_news_fires(conn):
    mqtt = _FakeMqtt()
    values = {"top_news_attrs": {"items": [
        {"title": "Nokia traci duży kontrakt", "impact": 3, "thesis_pl": "Poważne ryzyko.",
         "url": "https://example.com/a"},
    ]}}
    fired = alerts.check_and_fire(conn, _cfg(), values, mqtt)
    assert fired[0]["kind"] == "high_impact_news"
    assert fired[0]["url"] == "https://example.com/a"


def test_high_impact_news_ignores_lower_impact(conn):
    mqtt = _FakeMqtt()
    values = {"top_news_attrs": {"items": [{"title": "Zwykły news", "impact": 1}]}}
    fired = alerts.check_and_fire(conn, _cfg(), values, mqtt)
    assert fired == []


def test_high_impact_news_no_items_does_not_crash(conn):
    mqtt = _FakeMqtt()
    fired = alerts.check_and_fire(conn, _cfg(), {}, mqtt)
    assert fired == []


# --- odpalenie: alerts_log + mqtt + notify ---

def test_fire_writes_alerts_log_row(conn):
    mqtt = _FakeMqtt()
    alerts.check_and_fire(conn, _cfg(), {"change_pct_day": 5.0}, mqtt)
    row = conn.execute("SELECT * FROM alerts_log").fetchone()
    assert row["kind"] == "price_move_pct"
    assert row["severity"] == "info"
    import json
    payload = json.loads(row["payload"])
    assert payload["change_pct_day"] == 5.0


def test_fire_publishes_to_mqtt(conn):
    mqtt = _FakeMqtt()
    alerts.check_and_fire(conn, _cfg(), {"change_pct_day": 5.0}, mqtt)
    assert len(mqtt.published) == 1
    assert mqtt.published[0]["kind"] == "price_move_pct"
    assert "fired_at" in mqtt.published[0]


def test_fire_without_mqtt_pub_does_not_crash(conn):
    fired = alerts.check_and_fire(conn, _cfg(), {"change_pct_day": 5.0}, None)
    assert len(fired) == 1  # nadal loguje do alerts_log, po prostu bez MQTT


def test_fire_calls_notify_with_slash_converted_service(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify",
                        lambda service, title, message: calls.append((service, title, message)))
    alerts.check_and_fire(conn, _cfg(notify_service="notify.family"),
                          {"change_pct_day": 5.0}, None)
    assert calls == [("notify/family", "Nokia wzrósł o 5.0% dzisiaj", "Kurs wzrósł o +5.00% w ciągu dnia.")]


def test_fire_skips_notify_when_service_empty(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(ha_client, "notify", lambda *a: calls.append(a))
    alerts.check_and_fire(conn, _cfg(notify_service=""), {"change_pct_day": 5.0}, None)
    assert calls == []


# --- wiele alertów naraz ---

def test_multiple_alert_kinds_can_fire_in_one_check(conn):
    mqtt = _FakeMqtt()
    values = {"change_pct_day": 5.0, "rel_perf_1d_vs_omxh25": 7.0}
    fired = alerts.check_and_fire(conn, _cfg(), values, mqtt)
    kinds = {f["kind"] for f in fired}
    assert kinds == {"price_move_pct", "divergence"}
