"""Krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): formatowanie liczb po polsku
dla karty „Portfel" na pulpicie — separator tysięcy, przecinek dziesiętny,
`None` -> myślnik zamiast "None" w HTML (patrz test_dashboard_omits_pln_when_no_fx_rate)."""
from nokia_tracker.format import money, pct, qty

NBSP = " "


def test_money_adds_thousands_separator():
    assert money(143618) == f"143{NBSP}618"


def test_money_default_zero_decimals():
    # zaokrąglenie "do parzystej" (banker's rounding) z formatowania Pythona: 4902.5 -> 4902
    assert money(4902.5) == f"4{NBSP}902"


def test_money_with_decimals():
    assert money(4902.5, decimals=2) == f"4{NBSP}902,50"


def test_money_small_value_no_separator():
    assert money(65) == "65"


def test_money_negative():
    assert money(-1234) == f"-1{NBSP}234"


def test_money_zero():
    assert money(0) == "0"


def test_money_none_is_dash():
    assert money(None) == "—"


def test_money_signed_positive():
    assert money(22332, signed=True) == f"+22{NBSP}332"


def test_money_signed_negative_no_double_sign():
    assert money(-500, signed=True) == "-500"


def test_qty_two_decimals_and_separator():
    assert qty(2887.05134) == f"2{NBSP}887,05"


def test_qty_custom_decimals():
    assert qty(142.7294, decimals=4) == "142,7294"


def test_qty_none_is_dash():
    assert qty(None) == "—"


def test_pct_signed_positive():
    assert pct(2467.5) == "+2 467,5".replace(" ", NBSP)


def test_pct_signed_negative():
    assert pct(-12.3) == "-12,3"


def test_pct_unsigned():
    assert pct(12.3, signed=False) == "12,3"


def test_pct_none_is_dash():
    assert pct(None) == "—"
