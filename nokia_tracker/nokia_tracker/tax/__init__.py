"""Pakiet podatkowy 0.2.0 (BLUEPRINT §3a).

Re-eksport `compute_dividend_tax` z `dividends.py`, żeby dotychczasowe
`from . import tax as taxm; taxm.compute_dividend_tax(...)` w `sensors.py`
i `web.py` (oraz `tests/test_tax.py`) działało bez żadnej zmiany po
przejściu z płaskiego modułu `tax.py` na pakiet — kolizja nazw z
modułami blueprintu (`tax/lots.py`, `tax/policy.py`) wymagała pakietu,
ale API na zewnątrz zostaje identyczne.
"""
from .dividends import compute_dividend_tax

__all__ = ["compute_dividend_tax"]
