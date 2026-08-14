"""Krok 23 (docs/PLAN_KROK_23_portfel_kafelki.md): formatowanie liczb po polsku
dla karty „Portfel" na pulpicie (separator tysięcy, przecinek dziesiętny).

Rejestrowane jako filtry Jinja w `web.py::create_app`. Użyte w karcie Portfel na
pulpicie i (od kroku 25) na `/wyniki` — obie strony pokazują liczby zagregowane,
nie wymagające bajtowej zgodności z wyciągiem. Strony podatkowe (`/lots`, `/grants`,
`/pit38`) celowo zostają przy dotychczasowym `'%.Nf'|format(...)`, żeby nie zmieniać
liczb, które muszą się dać zestawić co do grosza z wyciągiem/PIT-38.

Separator tysięcy to znak spacji nierozdzielającej U+00A0 (NIE encja `&nbsp;` —
Jinja z autoescape zamieniłaby `&` na `&amp;` i zepsuła encję w renderowanym HTML).
Każda funkcja zwraca „—" dla `None`, nigdy pythonowy napis "None" (patrz
test_dashboard_omits_pln_when_no_fx_rate w test_web.py)."""
from __future__ import annotations

NBSP = " "
DASH = "—"


def _grouped(value: float, decimals: int) -> str:
    """`f'{value:,.Nf}'` daje separator `,` i kropkę dziesiętną (locale en_US) —
    zamieniamy na spację nierozdzielającą + przecinek (konwencja polska)."""
    sign = "-" if value < 0 else ""
    formatted = f"{abs(value):,.{decimals}f}"
    integer_part, _, fraction_part = formatted.partition(".")
    integer_part = integer_part.replace(",", NBSP)
    if decimals:
        return f"{sign}{integer_part},{fraction_part}"
    return f"{sign}{integer_part}"


def money(value: float | None, decimals: int = 0, signed: bool = False) -> str:
    """Kwoty PLN/EUR w karcie Portfel, np. `money(143618)` -> "143 618",
    `money(4902.5, decimals=2)` -> "4 902,50" (spacja = NBSP). `signed=True`
    dopisuje `+` dla wartości nieujemnych (np. niezrealizowany P&L) — ujemne
    już mają `-` z `_grouped`, więc nie dublujemy znaku."""
    if value is None:
        return DASH
    formatted = _grouped(value, decimals)
    if signed and value >= 0:
        formatted = f"+{formatted}"
    return formatted


def qty(value: float | None, decimals: int = 2) -> str:
    """Ilość akcji skrócona do 2 miejsc (domyślnie) dla czytelności kafelków —
    pełne 4 miejsca (jak reszta apki) zostają dostępne przez `decimals=4` tam,
    gdzie liczy się zgodność co do grosza z wyciągiem (np. ostrzeżenie o zaległych
    transzach)."""
    if value is None:
        return DASH
    return _grouped(value, decimals)


def pct(value: float | None, decimals: int = 1, signed: bool = True) -> str:
    """Procent z opcjonalnym znakiem (`+2 467,5`), np. całkowity zwrot portfela."""
    if value is None:
        return DASH
    formatted = _grouped(value, decimals)
    if signed and value >= 0:
        formatted = f"+{formatted}"
    return formatted
