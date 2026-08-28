import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nokia_tracker import db as dbm  # noqa: E402
from nokia_tracker import settings as settingsm  # noqa: E402
from nokia_tracker.tax import dividends as taxdiv  # noqa: E402
from nokia_tracker.tax import lots as taxlots  # noqa: E402
from nokia_tracker.web import create_app  # noqa: E402


@pytest.fixture
def db_path(tmp_path):
    """Ścieżka do pustej, zmigrowanej bazy — wspólna dla `conn`/`client`/`seed`
    w tym samym teście (pytest cache'uje `tmp_path` per test, więc wszystkie
    trzy fixture'y otwierają połączenia do TEGO SAMEGO pliku)."""
    path = str(tmp_path / "test.db")
    c = dbm.get_conn(path)
    dbm.migrate(c)
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = dbm.get_conn(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path):
    """Klient testowy Flaska na pustej, zmigrowanej bazie. Wspólny dla
    wszystkich `tests/test_web_*.py` (E3 — docs/ROADMAP_V3.md; dawniej
    lokalny w `test_web.py`, ~100 konsumentów po podziale na wiele plików)."""
    app = create_app(db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class _Seed:
    """Etap 3 (docs/PLAN_0_28_0_ui_porzadki.md): zasiew danych bezpośrednio
    przez silnik (`tax/lots.py`, `tax/dividends.py`, `settings.py`) —
    dokładnie te same funkcje, które wołały usunięte trasy `POST /lots`,
    `POST /lots/sell`, `POST /portfolio`, `POST /dividends` (mapowanie pól
    formularza→argumentów skopiowane 1:1 z tamtych handlerów). Testy, które
    wcześniej zasiewały dane przez HTTP POST na te trasy, przechodzą na
    `seed.*` — asercje o SKUTKACH (FIFO, kwoty, błędy silnika) zostają bez
    zmiany, bo silnik jest ten sam."""

    def __init__(self, conn):
        self.conn = conn

    def lot(self, acquired_date, quantity, price_eur, lot_type="own", fee_eur=0.0,
            **kwargs):
        return taxlots.add_lot(
            self.conn, acquired_date, lot_type, quantity, price_eur,
            fee_eur=fee_eur, **kwargs)

    def sale(self, sale_date, quantity, price_eur, fee_eur=0.0, proceeds_eur=None):
        return taxlots.record_sale(
            self.conn, sale_date, quantity, price_eur, fee_eur=fee_eur,
            proceeds_eur=proceeds_eur)

    def position(self, position_qty, avg_cost_eur):
        settingsm.set_settings(
            self.conn, {"position_qty": position_qty, "avg_cost_eur": avg_cost_eur})

    def dividend(self, pay_date, gross_eur, withholding_pct=None, quantity=None,
                 gross_per_share_eur=None, drip_purchase_date=None,
                 drip_price_eur=None, drip_shares=None):
        cfg = settingsm.get_settings(self.conn)
        wp = withholding_pct if withholding_pct is not None else cfg["finnish_withholding_pct"]
        taxes_eur = gross_eur * wp / 100
        natural_key = f"manual:{pay_date}:{gross_eur}:{quantity or 0}:{wp}"
        return taxdiv.add_dividend(
            self.conn, record_date=pay_date, entitled_quantity=quantity or 0.0,
            gross_eur=gross_eur, taxes_eur=taxes_eur,
            gross_per_share_eur=gross_per_share_eur,
            purchase_date=drip_purchase_date, purchase_price_eur=drip_price_eur,
            purchased_shares=drip_shares, natural_key=natural_key)


@pytest.fixture
def seed(db_path):
    c = dbm.get_conn(db_path)
    yield _Seed(c)
    c.close()
