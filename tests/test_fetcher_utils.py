from datetime import date
from data.fetcher import _prev_weekday


def test_sunday_maps_to_friday():
    assert _prev_weekday(date(2026, 6, 28)) == date(2026, 6, 26)


def test_saturday_maps_to_friday():
    assert _prev_weekday(date(2026, 6, 27)) == date(2026, 6, 26)


def test_monday_maps_to_friday():
    assert _prev_weekday(date(2026, 6, 29)) == date(2026, 6, 26)


def test_wednesday_maps_to_tuesday():
    assert _prev_weekday(date(2026, 7, 1)) == date(2026, 6, 30)
