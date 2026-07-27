"""Testy czystych funkcji publishera — bez żywego brokera MQTT
(BLUEPRINT §5: zero żywego I/O w testach)."""
from nokia_tracker.publisher import (_ENTITIES, discovery_payloads,
                                     render_values)


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
