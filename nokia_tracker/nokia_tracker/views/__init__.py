"""Warstwa składania danych dla web UI (E3 — docs/ROADMAP_V3.md).

Funkcje tutaj biorą `conn` + argumenty zwykłe (nigdy `flask.request`,
`url_for`, `render_template`) i zwracają zwykły `dict` gotowy do
`render_template(**...)`. Nigdy nie zapisują do bazy — zapisy zostają w
trasach pod `dbm.WRITE_LOCK`, żeby moment zapisu był widoczny w jednym
miejscu. Dzięki temu logika składania jest wywoływalna spoza Flaska (sensory
MQTT, przyszłe „Stan konta" z E5) i testowalna bez klienta HTTP.
"""
