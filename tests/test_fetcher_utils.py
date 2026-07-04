from datetime import date

import pandas as pd
import data.fetcher as fetcher
from data.fetcher import _prev_weekday


def _index_df(closes, start='2026-06-22'):
    dates = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({
        'Open':   closes,
        'High':   [c * 1.01 for c in closes],
        'Low':    [c * 0.99 for c in closes],
        'Close':  closes,
        'Volume': [1_000_000] * len(closes),
    }, index=dates)


class _FakeFastInfo:
    last_price = 999.0


class _FakeTicker:
    def __init__(self, *_): pass
    fast_info = _FakeFastInfo()


def test_index_patch_does_not_append_new_rows(monkeypatch):
    """지수 패치가 존재하지 않는 날짜 행(공휴일일 수 있음)을 만들어내면 안 된다."""
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)
    df = _index_df([100.0, 101.0, 102.0, 103.0, 104.0])   # 마지막 행 완전, 날짜는 과거
    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')
    assert len(out) == len(df)
    assert float(out['Close'].iloc[-1]) == 104.0


def test_index_patch_fills_nan_close_on_existing_row(monkeypatch):
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)
    df = _index_df([100.0, 101.0, 102.0, 103.0, 104.0])
    df.loc[df.index[-1], 'Close'] = float('nan')
    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')
    assert len(out) == len(df)
    assert float(out['Close'].iloc[-1]) == 999.0


def test_sunday_maps_to_friday():
    assert _prev_weekday(date(2026, 6, 28)) == date(2026, 6, 26)


def test_saturday_maps_to_friday():
    assert _prev_weekday(date(2026, 6, 27)) == date(2026, 6, 26)


def test_monday_maps_to_friday():
    assert _prev_weekday(date(2026, 6, 29)) == date(2026, 6, 26)


def test_wednesday_maps_to_tuesday():
    assert _prev_weekday(date(2026, 7, 1)) == date(2026, 6, 30)
