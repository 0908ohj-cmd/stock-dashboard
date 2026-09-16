import numpy as np
import pandas as pd

from data import fetcher


def _df(rows):
    """rows: (날짜, Open, High, Low, Close, Volume)"""
    return pd.DataFrame(
        {
            'Open':   [r[1] for r in rows],
            'High':   [r[2] for r in rows],
            'Low':    [r[3] for r in rows],
            'Close':  [r[4] for r in rows],
            'Volume': [r[5] for r in rows],
        },
        index=pd.to_datetime([r[0] for r in rows]),
    )


class _FakeTicker:
    """yf.Ticker 대역 — fast_info.last_price만 쓴다."""
    def __init__(self, *a, **k):
        pass

    @property
    def fast_info(self):
        return type('F', (), {'last_price': 9999.0})()


def test_today_patch_does_not_create_row(monkeypatch):
    """당일 행이 없어도 새 행을 만들지 않는다.

    last_price는 현재가 한 점이라 O/H/L을 만들 수 없고, O/H/L이 NaN인 행은
    detect_jjin_bounce가 통째로 건너뛰어 그날을 판정 불능으로 만든다.
    """
    df = _df([('2026-09-08', 1.0, 2.0, 0.5, 1.5, 100.0)])
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)

    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')

    assert list(out.index) == list(df.index)        # 행이 늘지 않았다
    assert float(out['Close'].iloc[-1]) == 1.5      # 확정 종가를 덮어쓰지 않는다


def test_today_patch_fills_nan_close(monkeypatch):
    """이미 있는 행의 Close가 NaN이면 현재가로 채운다."""
    df = _df([('2026-09-08', 1.0, 2.0, 0.5, np.nan, 100.0)])
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)

    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')

    assert float(out['Close'].iloc[-1]) == 9999.0
    assert len(out) == 1


def test_incomplete_ohlc_dates_catches_nan_and_flat():
    """NaN 행과 O=H=L=C 평탄 행을 모두 복구 대상으로 잡는다.

    NaN끼리의 비교는 항상 False라 평탄 판정만으로는 NaN 행이 빠진다 —
    2026-09-09 코스피·코스닥 지수가 정확히 이 구멍으로 복구되지 못했다.
    """
    df = _df([
        ('2026-09-07', 1.0, 2.0, 0.5, 1.5, 100.0),                     # 정상
        ('2026-09-08', 3.0, 3.0, 3.0, 3.0, 100.0),                     # 평탄
        ('2026-09-09', np.nan, np.nan, np.nan, 7051.64, 322211.0),     # O/H/L NaN
    ])

    got = [str(ts.date()) for ts in fetcher._incomplete_ohlc_dates(df)]

    assert got == ['2026-09-08', '2026-09-09']
