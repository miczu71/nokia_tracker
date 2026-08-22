import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nokia_tracker import db as dbm  # noqa: E402
from nokia_tracker.web import create_app  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = dbm.get_conn(str(tmp_path / "test.db"))
    dbm.migrate(c)
    yield c
    c.close()


@pytest.fixture
def client(tmp_path):
    """Klient testowy Flaska na pustej, zmigrowanej bazie. Wspólny dla
    wszystkich `tests/test_web_*.py` (E3 — docs/ROADMAP_V3.md; dawniej
    lokalny w `test_web.py`, ~100 konsumentów po podziale na wiele plików)."""
    db_path = str(tmp_path / "test.db")
    conn = dbm.get_conn(db_path)
    dbm.migrate(conn)
    conn.close()
    app = create_app(db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
