"""MQTT discovery publisher — jedno urządzenie 'Nokia Tracker', płaska
przestrzeń topików (BLUEPRINT §2). Wzorzec discovery/LWT/retained/zaległy
stan przy _on_connect portowany z fuel_tracker/publisher.py; API klienta —
z pv_roi_tracker/publisher.py (paho-mqtt==2.1.0, CallbackAPIVersion.VERSION2 —
v1 callbacks znikają w paho 3.x, patrz requirements.txt).

sensor i binary_sensor dzielą jedną listę encji (`domain` w _Entity) —
prostsze niż dwie równoległe struktury, bo oba typy mają identyczny
kształt discovery poza segmentem domeny w topiku.
"""
from __future__ import annotations

import json
import logging
from typing import NamedTuple, Optional

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

_DEVICE_ID = "nokia_tracker"
_DEVICE_NAME = "Nokia Tracker"
_AVAIL_TOPIC = "nokia_tracker/availability"
_STATE_PREFIX = "nokia_tracker/sensors"
_DISC_PREFIX = "homeassistant"
_ALERT_TOPIC = "nokia_tracker/events/alert"


class _Entity(NamedTuple):
    domain: str          # 'sensor' | 'binary_sensor'
    slug: str
    name: str
    unit: Optional[str] = None
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    icon: Optional[str] = None
    has_attrs: bool = False  # publikuje też json_attributes_topic z values[f'{slug}_attrs']


# Kursy: unit EUR BEZ device_class (żeby state_class='measurement' dało
# wykresy long-term — 'monetary' nie współpracuje z 'measurement', patrz
# BLUEPRINT §2 pułapka #2). Wartości portfela w kolejnych krokach dostaną
# device_class='monetary' + state_class='total'.
_ENTITIES: list[_Entity] = [
    # --- rynek ---
    _Entity("sensor", "price_eur", "Price EUR", "EUR", None, "measurement", "mdi:chart-line"),
    _Entity("sensor", "change_pct_day", "Change Pct Day", "%", None, "measurement", "mdi:percent"),
    _Entity("sensor", "change_abs_day", "Change Abs Day", "EUR", None, "measurement", "mdi:cash-plus"),
    _Entity("sensor", "day_high", "Day High", "EUR", None, "measurement", "mdi:arrow-top-right"),
    _Entity("sensor", "day_low", "Day Low", "EUR", None, "measurement", "mdi:arrow-bottom-right"),
    _Entity("sensor", "prev_close", "Prev Close", "EUR", None, "measurement", "mdi:history"),
    _Entity("sensor", "volume", "Volume", None, None, "measurement", "mdi:chart-bar"),
    _Entity("sensor", "week52_high", "Week52 High", "EUR", None, "measurement", "mdi:arrow-up-bold"),
    _Entity("sensor", "week52_low", "Week52 Low", "EUR", None, "measurement", "mdi:arrow-down-bold"),
    _Entity("sensor", "market_state", "Market State", None, None, None, "mdi:store-clock"),
    _Entity("sensor", "last_quote_ts", "Last Quote Ts", None, "timestamp", None, "mdi:clock-outline"),
    # --- technika ---
    _Entity("sensor", "sma_20", "SMA 20", "EUR", None, "measurement", "mdi:chart-timeline-variant"),
    _Entity("sensor", "sma_50", "SMA 50", "EUR", None, "measurement", "mdi:chart-timeline-variant"),
    _Entity("sensor", "rsi_14", "RSI 14", None, None, "measurement", "mdi:gauge"),
    _Entity("sensor", "volatility_30d_pct", "Volatility 30D Pct", "%", None, "measurement",
           "mdi:chart-bell-curve"),
    _Entity("sensor", "trend", "Trend", None, None, None, "mdi:trending-up"),
    # --- benchmark ---
    _Entity("sensor", "ericsson_price", "Ericsson Price", "SEK", None, "measurement", "mdi:chart-line"),
    _Entity("sensor", "omxh25_value", "OMXH25 Value", None, None, "measurement", "mdi:finance"),
    _Entity("sensor", "rel_perf_1d_vs_omxh25", "Rel Perf 1D Vs OMXH25", "%", None, "measurement",
           "mdi:scale-balance"),
    _Entity("sensor", "rel_perf_1m_vs_ericsson", "Rel Perf 1M Vs Ericsson", "%", None,
           "measurement", "mdi:scale-balance"),
    _Entity("sensor", "beta_60d", "Beta 60D", None, None, "measurement", "mdi:chart-scatter-plot"),
    _Entity("sensor", "alpha_verdict", "Alpha Verdict", None, None, None, "mdi:magnify-scan"),
    # --- bliźniaki PLN + ADR ---
    _Entity("sensor", "eurpln_rate", "EURPLN Rate", None, None, "measurement", "mdi:currency-eur"),
    _Entity("sensor", "price_pln", "Price PLN", "PLN", None, "measurement", "mdi:cash"),
    _Entity("sensor", "adr_price_usd", "ADR Price USD", "USD", None, "measurement", "mdi:cash-100"),
    _Entity("sensor", "spread_vs_adr", "Spread Vs ADR", "%", None, "measurement", "mdi:compare"),
    # --- AI ---
    _Entity("sensor", "sentiment_score", "Sentiment Score", None, None, "measurement",
           "mdi:emoticon-outline"),
    _Entity("sensor", "sentiment_label", "Sentiment Label", None, None, None,
           "mdi:emoticon-outline"),
    _Entity("sensor", "impact_score", "Impact Score", None, None, "measurement",
           "mdi:alert-decagram-outline"),
    _Entity("sensor", "news_count_24h", "News Count 24H", None, None, "measurement",
           "mdi:newspaper-variant-outline"),
    _Entity("sensor", "top_news", "Top News", None, None, None, "mdi:newspaper",
           has_attrs=True),
    _Entity("sensor", "ai_provider_active", "AI Provider Active", None, None, None,
           "mdi:robot-outline"),
    _Entity("sensor", "ai_calls_today", "AI Calls Today", None, None, "measurement",
           "mdi:counter"),
    # --- prognozy i rekomendacja ---
    _Entity("sensor", "forecast_1w_eur", "Forecast 1W EUR", "EUR", None, "measurement",
           "mdi:crystal-ball", has_attrs=True),
    _Entity("sensor", "forecast_1m_eur", "Forecast 1M EUR", "EUR", None, "measurement",
           "mdi:crystal-ball", has_attrs=True),
    _Entity("sensor", "forecast_12m_eur", "Forecast 12M EUR", "EUR", None, "measurement",
           "mdi:crystal-ball", has_attrs=True),
    _Entity("sensor", "forecast_accuracy_pct", "Forecast Accuracy Pct", "%", None,
           "measurement", "mdi:target"),
    _Entity("sensor", "daily_briefing", "Daily Briefing", None, None, None,
           "mdi:text-box-outline", has_attrs=True),
    _Entity("sensor", "ai_recommendation", "AI Recommendation", None, None, None,
           "mdi:lightbulb-on-outline", has_attrs=True),
    # --- portfel (device_class=monetary+state_class=total dla wartości
    # portfela; avg_cost_eur to CENA per akcję jak price_eur — measurement,
    # bez device_class, patrz BLUEPRINT §2 pułapka #2) ---
    _Entity("sensor", "position_qty", "Position Qty", None, None, "measurement",
           "mdi:chart-donut"),
    _Entity("sensor", "avg_cost_eur", "Avg Cost EUR", "EUR", None, "measurement",
           "mdi:cash-marker"),
    _Entity("sensor", "cost_basis_eur", "Cost Basis EUR", "EUR", "monetary", "total",
           "mdi:cash-multiple"),
    _Entity("sensor", "market_value_eur", "Market Value EUR", "EUR", "monetary", "total",
           "mdi:briefcase-variant"),
    _Entity("sensor", "unrealized_pnl_eur", "Unrealized PnL EUR", "EUR", "monetary", "total",
           "mdi:trending-up"),
    _Entity("sensor", "unrealized_pnl_pct", "Unrealized PnL Pct", "%", None, "measurement",
           "mdi:percent"),
    _Entity("sensor", "total_return_pct", "Total Return Pct", "%", None, "measurement",
           "mdi:percent-outline"),
    _Entity("sensor", "market_value_pln", "Market Value PLN", "PLN", "monetary", "total",
           "mdi:briefcase-variant"),
    _Entity("sensor", "cost_basis_pln", "Cost Basis PLN", "PLN", "monetary", "total",
           "mdi:cash-multiple"),
    _Entity("sensor", "unrealized_pnl_pln", "Unrealized PnL PLN", "PLN", "monetary", "total",
           "mdi:trending-up"),
    # --- dywidendy i podatki (kalkulator orientacyjny, patrz sensors.py) ---
    _Entity("sensor", "dividends_gross_eur", "Dividends Gross EUR", "EUR", "monetary",
           "total", "mdi:cash-plus"),
    _Entity("sensor", "dividends_net_eur", "Dividends Net EUR", "EUR", "monetary",
           "total", "mdi:cash-check"),
    _Entity("sensor", "withholding_paid_eur", "Withholding Paid EUR", "EUR", "monetary",
           "total", "mdi:bank-minus"),
    _Entity("sensor", "pl_tax_due_eur", "PL Tax Due EUR", "EUR", "monetary", "total",
           "mdi:bank-outline"),
    _Entity("sensor", "reclaimable_from_finland_eur", "Reclaimable From Finland EUR", "EUR",
           "monetary", "total", "mdi:cash-refund"),
    _Entity("sensor", "dividend_yield_on_cost_pct", "Dividend Yield On Cost Pct", "%", None,
           "measurement", "mdi:percent-box-outline"),
    # --- loty i FIFO (krok 12, BLUEPRINT §3a) ---
    _Entity("sensor", "lots_total_qty", "Lots Total Qty", None, None, "measurement",
           "mdi:layers-outline"),
    _Entity("sensor", "lots_open_count", "Lots Open Count", None, None, "measurement",
           "mdi:format-list-numbered", has_attrs=True),
    _Entity("sensor", "lots_cost_basis_pln", "Lots Cost Basis PLN", "PLN", "monetary",
           "total", "mdi:cash-multiple"),
    _Entity("sensor", "realized_income_pln", "Realized Income PLN", "PLN", "monetary",
           "total", "mdi:cash-check"),
    _Entity("sensor", "realized_tax_pln", "Realized Tax PLN", "PLN", "monetary",
           "total", "mdi:bank-outline"),
    # --- granty ESPP/LTI (krok 13.5) ---
    _Entity("sensor", "unvested_qty", "Unvested Qty", None, None, "measurement",
           "mdi:timer-sand"),
    _Entity("sensor", "next_vest_date", "Next Vest Date", None, "date", None,
           "mdi:calendar-clock", has_attrs=True),
    # --- PIT-38 i symulacja (krok 15, BLUEPRINT §3a) ---
    _Entity("sensor", "pit38_income_pln", "PIT38 Income PLN", "PLN", "monetary",
           "total", "mdi:file-document-outline"),
    _Entity("sensor", "pit38_tax_pln", "PIT38 Tax PLN", "PLN", "monetary", "total",
           "mdi:bank-outline"),
    _Entity("sensor", "pit38_dividend_due_pln", "PIT38 Dividend Due PLN", "PLN",
           "monetary", "total", "mdi:cash-plus"),
    _Entity("sensor", "pit38_reclaimable_pln", "PIT38 Reclaimable PLN", "PLN",
           "monetary", "total", "mdi:cash-refund"),
    _Entity("sensor", "whatif_sell_all_tax_pln", "Whatif Sell All Tax PLN", "PLN",
           "monetary", "total", "mdi:calculator-variant-outline"),
    # --- wyniki: XIRR/TWR/atrybucja/benchmark (krok 25, 0.9.0) ---
    _Entity("sensor", "xirr_own_pct", "XIRR Own Pct", "%", None, "measurement",
           "mdi:chart-line-variant"),
    _Entity("sensor", "twr_pct", "TWR Pct", "%", None, "measurement",
           "mdi:chart-bell-curve-cumulative"),
    _Entity("sensor", "fx_effect_pln", "FX Effect PLN", "PLN", "monetary",
           "total", "mdi:currency-eur"),
    _Entity("sensor", "benchmark_omxh25_counterfactual_pln",
           "Benchmark OMXH25 Counterfactual PLN", "PLN", "monetary", "total",
           "mdi:swap-horizontal"),
    # --- doradca planu pracowniczego (krok 26, 0.10.0) ---
    _Entity("sensor", "forfeit_value_pln", "Forfeit Value PLN", "PLN", "monetary",
           "total", "mdi:cash-remove"),
    _Entity("sensor", "concentration_pct", "Concentration Pct", "%", None,
           "measurement", "mdi:chart-pie"),
    _Entity("sensor", "vest_this_year_qty", "Vest This Year Qty", None, None,
           "measurement", "mdi:calendar-check"),
    # --- straty z lat ubiegłych (krok 27, 0.11.0) ---
    _Entity("sensor", "loss_available_pln", "Loss Available PLN", "PLN",
           "monetary", "total", "mdi:trending-down"),
    _Entity("sensor", "loss_used_this_year_pln", "Loss Used This Year PLN", "PLN",
           "monetary", "total", "mdi:cash-minus"),
    # --- kalendarz dywidend (krok 30, 0.14.0) — nazwy świadomie omijają
    # `forecast_*`, zajęte przez prognozy CENOWE (forecast_1w_eur i in.) ---
    _Entity("sensor", "next_dividend_date", "Next Dividend Date", None, "date", None,
           "mdi:calendar-cash", has_attrs=True),
    _Entity("sensor", "dividend_next_12m_gross_eur", "Dividend Next 12M Gross EUR",
           "EUR", "monetary", "total", "mdi:cash-clock"),
    _Entity("sensor", "dividend_next_12m_net_pln", "Dividend Next 12M Net PLN",
           "PLN", "monetary", "total", "mdi:cash-check"),
    # --- binary ---
    _Entity("binary_sensor", "market_open", "Market Open", None, None, None,
           "mdi:store-clock-outline"),
]


def _state_topic(slug: str) -> str:
    return f"{_STATE_PREFIX}/{slug}/state"


def _attrs_topic(slug: str) -> str:
    return f"{_STATE_PREFIX}/{slug}/attrs"


def _disc_topic(domain: str, slug: str) -> str:
    return f"{_DISC_PREFIX}/{domain}/{_DEVICE_ID}/{slug}/config"


def _discovery_topics() -> list[str]:
    return [_disc_topic(e.domain, e.slug) for e in _ENTITIES]


def render_values(values: dict) -> dict[str, str]:
    """Mapa slug → payload tekstowy; brakujące wartości = 'unknown'."""
    out: dict[str, str] = {}
    for e in _ENTITIES:
        v = values.get(e.slug)
        if e.domain == "binary_sensor":
            out[e.slug] = "ON" if v else "OFF"
            continue
        if v is None:
            out[e.slug] = "unknown"
        elif isinstance(v, float):
            out[e.slug] = str(round(v, 4))
        else:
            out[e.slug] = str(v)
    return out


def render_attrs(values: dict) -> dict[str, str]:
    """Mapa slug → JSON atrybutów, tylko dla encji has_attrs z danymi
    w values pod kluczem f'{slug}_attrs'."""
    out: dict[str, str] = {}
    for e in _ENTITIES:
        if not e.has_attrs:
            continue
        attrs = values.get(f"{e.slug}_attrs")
        if attrs is not None:
            out[e.slug] = json.dumps(attrs, ensure_ascii=False)
    return out


def discovery_payloads(version: str) -> dict[str, dict]:
    """Mapa topic → payload discovery (wydzielone dla testów)."""
    device = {
        "identifiers": [_DEVICE_ID],
        "name": _DEVICE_NAME,
        "manufacturer": "Custom",
        "model": "nokia_tracker",
        "sw_version": version,
    }
    payloads: dict[str, dict] = {}
    for e in _ENTITIES:
        p: dict = {
            "name": e.name,
            "unique_id": f"{_DEVICE_ID}_{e.slug}",
            # object_id wymusza entity_id = sensor.<object_id> niezależnie od
            # 'name' — bez tego HA składa entity_id z device.name + name przy
            # PIERWSZEJ rejestracji i już go nie zmienia (złapane na żywo
            # w kroku 7: 'Forecast 1W' bez 'EUR' w nazwie dało entity_id bez
            # _eur mimo slugu forecast_1w_eur; patrz reference_mqtt_entity_naming).
            "object_id": f"{_DEVICE_ID}_{e.slug}",
            "state_topic": _state_topic(e.slug),
            "availability_topic": _AVAIL_TOPIC,
            "device": device,
        }
        if e.unit:
            p["unit_of_measurement"] = e.unit
        if e.device_class:
            p["device_class"] = e.device_class
        if e.state_class:
            p["state_class"] = e.state_class
        if e.icon:
            p["icon"] = e.icon
        if e.has_attrs:
            p["json_attributes_topic"] = _attrs_topic(e.slug)
        payloads[_disc_topic(e.domain, e.slug)] = p
    return payloads


class MQTTPublisher:
    def __init__(self, host: str, port: int, user: str, password: str,
                version: str = "0.0.0") -> None:
        self._host = host
        self._port = port
        self._version = version
        self._connected = False
        # Ostatni znany stan — pierwsza publikacja po starcie zwykle
        # wyprzedza connect (scheduler odpala tick natychmiast), więc stan
        # czeka tu i wychodzi w _on_connect (wzorzec z fuel_tracker).
        self._last_values: dict | None = None

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=_DEVICE_ID, clean_session=True)
        if user:
            self._client.username_pw_set(user, password)
        self._client.will_set(_AVAIL_TOPIC, "offline", retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def connect(self) -> None:
        self._client.connect_async(self._host, self._port, keepalive=60)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.publish(_AVAIL_TOPIC, "offline", retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            self._connected = True
            logger.info("MQTT połączone z %s:%d", self._host, self._port)
            client.publish(_AVAIL_TOPIC, "online", retain=True)
            self._publish_discovery()
            if self._last_values is not None:
                self._publish_values(self._last_values)
                logger.info("MQTT: opublikowano zaległy stan sprzed połączenia")
        else:
            logger.error("MQTT connect nieudany (reason_code=%s)", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None) -> None:
        self._connected = False
        if reason_code != 0:
            logger.warning("MQTT rozłączone (reason_code=%s) — paho wznowi połączenie",
                          reason_code)

    def publish(self, values: dict) -> None:
        self._last_values = values
        if not self._connected:
            logger.debug("MQTT niepołączone — stan zapamiętany do on_connect")
            return
        self._publish_discovery()
        self._publish_values(values)

    def publish_alert(self, alert: dict) -> None:
        """Publikuje zdarzenie alertu — NIE retained (BLUEPRINT §2: do
        konsumpcji przez automatyzacje HA, nie stan trwały). Cichy no-op,
        gdy MQTT akurat rozłączone — alert i tak trafił do alerts_log."""
        if not self._connected:
            logger.debug("MQTT niepołączone — alert %s pominięty (jest w alerts_log)",
                         alert.get("kind"))
            return
        self._client.publish(_ALERT_TOPIC, json.dumps(alert, ensure_ascii=False))

    def unpublish(self) -> None:
        """Czyści retained discovery configs — HA usuwa encje urządzenia."""
        for topic in _discovery_topics():
            self._client.publish(topic, "", retain=True)

    def _publish_discovery(self) -> None:
        for topic, payload in discovery_payloads(self._version).items():
            self._client.publish(topic, json.dumps(payload), retain=True)

    def _publish_values(self, values: dict) -> None:
        for slug, payload in render_values(values).items():
            self._client.publish(_state_topic(slug), payload, retain=True)
        for slug, payload in render_attrs(values).items():
            self._client.publish(_attrs_topic(slug), payload, retain=True)
        logger.debug("Opublikowano stan sensorów MQTT")
