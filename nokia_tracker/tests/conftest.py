import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nokia_tracker import db as dbm  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = dbm.get_conn(str(tmp_path / "test.db"))
    dbm.migrate(c)
    yield c
    c.close()
