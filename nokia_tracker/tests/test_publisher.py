"""Testy czystych funkcji publishera — bez żywego brokera MQTT
(BLUEPRINT §5: zero żywego I/O w testach)."""
from nokia_tracker.publisher import (_ENTITIES, discovery_payloads,
                                     render_attrs, render_values)


def test_discovery_payloads_has_topic_per_entity():
    payloads = discovery_payloads("0.1.0")
    assert len(payloads) == len(_ENTITIES)


def test_discovery_payload_sensor_shape():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/price_eur/config"]
    assert p["name"] == "Price EUR"
    assert p["unique_id"] == "nokia_tracker_price_eur"
    assert p["state_topic"] == "nokia_tracker/sensors/price_eur/state"
    assert p["availability_topic"] == "nokia_tracker/availability"
    assert p["unit_of_measurement"] == "EUR"
    assert p["state_class"] == "measurement"
    assert "device_class" not in p  # pułapka #2 z BLUEPRINT: EUR bez device_class monetary
    assert p["device"]["identifiers"] == ["nokia_tracker"]
    assert p["device"]["sw_version"] == "0.1.0"


def test_discovery_payload_binary_sensor_shape():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/binary_sensor/nokia_tracker/market_open/config"]
    assert p["name"] == "Market Open"
    assert p["unique_id"] == "nokia_tracker_market_open"
    assert "unit_of_measurement" not in p


def test_discovery_payload_omits_absent_optional_fields():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/trend/config"]
    assert "unit_of_measurement" not in p
    assert "device_class" not in p
    assert "state_class" not in p


def test_render_values_rounds_floats():
    values = {"price_eur": 8.2619999999}
    out = render_values(values)
    assert out["price_eur"] == "8.262"


def test_render_values_missing_key_is_unknown():
    out = render_values({})
    assert out["price_eur"] == "unknown"
    assert out["sma_20"] == "unknown"


def test_render_values_binary_sensor_on_off():
    assert render_values({"market_open": True})["market_open"] == "ON"
    assert render_values({"market_open": False})["market_open"] == "OFF"
    assert render_values({})["market_open"] == "OFF"  # brak = domyślnie zamknięta


def test_render_values_passes_through_strings():
    out = render_values({"trend": "silny wzrost", "market_state": "sesja otwarta"})
    assert out["trend"] == "silny wzrost"
    assert out["market_state"] == "sesja otwarta"


def test_discovery_payload_ericsson_uses_sek():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/ericsson_price/config"]
    assert p["unit_of_measurement"] == "SEK"


def test_discovery_payload_alpha_verdict_has_no_unit():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/alpha_verdict/config"]
    assert "unit_of_measurement" not in p


def test_all_entity_slugs_unique():
    slugs = [e.slug for e in _ENTITIES]
    assert len(slugs) == len(set(slugs))


def test_discovery_payload_top_news_has_json_attributes_topic():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/top_news/config"]
    assert p["json_attributes_topic"] == "nokia_tracker/sensors/top_news/attrs"


def test_discovery_payload_sentiment_score_no_attributes_topic():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/sentiment_score/config"]
    assert "json_attributes_topic" not in p


def test_render_attrs_only_entities_with_has_attrs():
    out = render_attrs({"top_news_attrs": {"items": [1, 2]}, "sentiment_score_attrs": {"x": 1}})
    assert out == {"top_news": '{"items": [1, 2]}'}


def test_render_attrs_missing_key_omitted():
    assert render_attrs({}) == {}


def test_discovery_payload_forecast_1w_has_json_attributes_topic():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/forecast_1w_eur/config"]
    assert p["json_attributes_topic"] == "nokia_tracker/sensors/forecast_1w_eur/attrs"
    assert p["unit_of_measurement"] == "EUR"


def test_discovery_payload_ai_recommendation_has_json_attributes_topic():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/ai_recommendation/config"]
    assert p["json_attributes_topic"] == "nokia_tracker/sensors/ai_recommendation/attrs"


def test_discovery_payload_sets_object_id_to_force_stable_entity_id():
    # object_id wymusza entity_id = sensor.<object_id> niezależnie od 'name' —
    # zapobiega klasie błędu z kroku 7 (entity_id ustala się przy pierwszej
    # rejestracji z device.name+name i już się nie zmienia przy zmianie name).
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/forecast_1w_eur/config"]
    assert p["object_id"] == "nokia_tracker_forecast_1w_eur"
    assert p["object_id"] == p["unique_id"]


def test_discovery_payload_forecast_accuracy_pct_no_attributes_topic():
    payloads = discovery_payloads("0.1.0")
    p = payloads["homeassistant/sensor/nokia_tracker/forecast_accuracy_pct/config"]
    assert "json_attributes_topic" not in p
    assert p["unit_of_measurement"] == "%"
