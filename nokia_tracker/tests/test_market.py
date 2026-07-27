from datetime import datetime
from zoneinfo import ZoneInfo

from nokia_tracker import market

# 2026-07-28 = wtorek (zweryfikowane: date.weekday()==1), 2026-08-01 = sobota.
HEL = ZoneInfo("Europe/Helsinki")


def test_session_open_midday_weekday():
    assert market.is_session_open(datetime(2026, 7, 28, 12, 0, tzinfo=HEL)) is True


def test_session_closed_before_open():
    assert market.is_session_open(datetime(2026, 7, 28, 9, 59, tzinfo=HEL)) is False


def test_session_closed_after_close():
    assert market.is_session_open(datetime(2026, 7, 28, 18, 30, tzinfo=HEL)) is False


def test_session_open_exactly_at_open_boundary():
    assert market.is_session_open(datetime(2026, 7, 28, 10, 0, tzinfo=HEL)) is True


def test_session_open_just_before_close_boundary():
    assert market.is_session_open(datetime(2026, 7, 28, 18, 29, tzinfo=HEL)) is True


def test_session_closed_on_saturday():
    assert market.is_session_open(datetime(2026, 8, 1, 12, 0, tzinfo=HEL)) is False


def test_session_closed_on_sunday():
    assert market.is_session_open(datetime(2026, 8, 2, 12, 0, tzinfo=HEL)) is False


def test_converts_from_utc_correctly():
    # 2026-07-28 12:00 UTC = 15:00 EEST (UTC+3 latem) -> w sesji
    utc = ZoneInfo("UTC")
    assert market.is_session_open(datetime(2026, 7, 28, 12, 0, tzinfo=utc)) is True
    # 2026-07-28 16:00 UTC = 19:00 EEST -> po sesji
    assert market.is_session_open(datetime(2026, 7, 28, 16, 0, tzinfo=utc)) is False
