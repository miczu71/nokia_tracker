from nokia_tracker import settings as settingsm


def test_get_settings_returns_defaults_when_empty(conn):
    s = settingsm.get_settings(conn)
    assert s["poll_interval_minutes"] == 10
    assert s["cost_basis_policy"] == "own_only"
    assert s["ai_primary"] == "local"
    assert s["finnish_withholding_pct"] == 35.0


def test_seed_from_options_only_fills_missing_keys(conn):
    # Baza ma pierwszeństwo nad opcjami Supervisora po pierwszym starcie.
    settingsm.set_settings(conn, {"poll_interval_minutes": 5})
    settingsm.seed_from_options(conn, {"poll_interval_minutes": "10"})
    s = settingsm.get_settings(conn)
    assert s["poll_interval_minutes"] == 5


def test_seed_from_options_fills_new_key(conn):
    settingsm.seed_from_options(conn, {"tax_year": "2025"})
    s = settingsm.get_settings(conn)
    assert s["tax_year"] == 2025


def test_set_settings_overwrites(conn):
    settingsm.set_settings(conn, {"cost_basis_policy": "own_plus_drip"})
    settingsm.set_settings(conn, {"cost_basis_policy": "all_at_acquisition"})
    s = settingsm.get_settings(conn)
    assert s["cost_basis_policy"] == "all_at_acquisition"


def test_normalize_notify_service_slash_to_dot(conn):
    settingsm.set_settings(conn, {"notify_service": "notify/family"})
    s = settingsm.get_settings(conn)
    assert s["notify_service"] == "notify.family"


def test_unknown_key_ignored(conn):
    settingsm.set_settings(conn, {"nieistniejacy_klucz": "x"})
    settingsm.seed_from_options(conn, {"nieistniejacy_klucz": "y"})
    # brak wyjątku = sukces; klucz po prostu pominięty

# --- krok 26 (docs/PLAN_KROK_26_doradca.md): doradca planu pracowniczego ---

def test_other_net_worth_pln_defaults_to_zero(conn):
    s = settingsm.get_settings(conn)
    assert s["other_net_worth_pln"] == 0.0
    assert s["concentration_alert_pct"] == 25.0


def test_defaults_cover_every_settings_type():
    """Strażnik: bez tego `get_settings` wywala KeyError przy zapomnianym DEFAULT dla
    nowego klucza w SETTINGS_TYPES (dziś go brak — dodane w kroku 26)."""
    assert set(settingsm.DEFAULTS) == set(settingsm.SETTINGS_TYPES)
