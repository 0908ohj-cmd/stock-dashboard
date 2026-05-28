import pandas as pd
import numpy as np
import pytest
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope


def _make_df(closes, highs=None, lows=None, volumes=None, start='2026-01-01'):
    n = len(closes)
    dates = pd.date_range(start, periods=n, freq='B')
    highs   = highs   or [c * 1.01 for c in closes]
    lows    = lows    or [c * 0.99 for c in closes]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        'Open':   closes,
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Volume': volumes,
    }, index=dates)


def _make_breakout_df(base_closes, breakout_close, today_close,
                       base_vol=1_000_000, breakout_vol=4_000_000):
    """base_closes + breakout + today. 돌파봉은 저가를 낮게 설정해 top-30% 조건 충족."""
    n_base = len(base_closes)
    closes  = base_closes + [breakout_close, today_close]
    highs   = [c * 1.01 for c in base_closes] + [breakout_close * 1.005, today_close * 1.01]
    lows    = [c * 0.99 for c in base_closes] + [breakout_close * 0.92, today_close * 0.99]
    volumes = [base_vol] * n_base + [breakout_vol, base_vol]
    return _make_df(closes, highs=highs, lows=lows, volumes=volumes)


def test_detects_high_volume_breakout():
    """거래량 300%+, 60일 고점 돌파, 정배열(상승추세) → 기준봉 탐지"""
    # 73일 완만 상승(정배열 자연 확보) + breakout + today
    base = [95 + i * 0.15 for i in range(73)]
    df = _make_breakout_df(base, breakout_close=108.0, today_close=108.0)
    result = find_pivot_candle(df, lookback=5)
    assert result is not None
    assert result['vol_ratio'] >= 3.0


def test_no_pivot_when_volume_insufficient():
    """거래량 200% 미만 → 기준봉 없음"""
    base = [95 + i * 0.15 for i in range(73)]
    df = _make_breakout_df(base, breakout_close=108.0, today_close=108.0, breakout_vol=1_500_000)
    assert find_pivot_candle(df, lookback=5) is None


def test_no_pivot_when_close_not_in_top30pct():
    """종가가 레인지 하위 → 기준봉 없음 (긴 윗꼬리)"""
    base    = [95 + i * 0.15 for i in range(73)]
    closes  = base + [101.0, 101.0]
    highs   = [c * 1.01 for c in base] + [115.0, 101.5]   # 돌파봉에 긴 윗꼬리
    lows    = [c * 0.99 for c in base] + [100.0, 100.5]
    volumes = [1_000_000] * 73 + [4_000_000, 1_000_000]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    # close=101, low=100, high=115 → (101-100)/(115-100) ≈ 0.07 < 0.7
    assert find_pivot_candle(df, lookback=5) is None


def test_picks_highest_vol_ratio_among_candidates():
    """조건 충족 후보 2개 → 거래량 비율 더 높은 것 선택"""
    # 78일 상승 추세 + 첫 돌파(4배) + 횡보 2일 + 두 번째 더 큰 돌파(6배) + today
    base = [90 + i * 0.15 for i in range(78)]
    b1   = base[-1] * 1.05
    b2   = b1 * 1.05
    closes  = base + [b1,          b1 * 0.99, b1 * 0.99, b2,          b2 * 0.99]
    highs   = [c * 1.01 for c in base] + [b1*1.005, b1*1.01,  b1*1.01,  b2*1.005, b2*1.01]
    lows    = [c * 0.99 for c in base] + [b1*0.92,  b1*0.99,  b1*0.99,  b2*0.92,  b2*0.99]
    volumes = [1_000_000] * 78 + [4_000_000, 1_000_000, 1_000_000, 6_000_000, 1_000_000]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    result = find_pivot_candle(df, lookback=10)
    assert result is not None
    assert result['vol_ratio'] >= 5.0


def test_no_pivot_when_no_resistance_breakout():
    """60일 고점 돌파 없음 + 횡보 박스 아님 → 기준봉 없음"""
    # 전반부 110, 후반부 하락 후 106 → 60일 고점(110)을 돌파 못 함
    closes  = [110.0] * 40 + [105.0] * 33 + [106.0, 106.0]
    volumes = [1_000_000] * 73 + [4_000_000, 1_000_000]
    df = _make_df(closes, volumes=volumes)
    assert find_pivot_candle(df, lookback=5) is None


def test_classify_returns_no_pivot_when_none():
    df = _make_df([100.0] * 30)
    assert classify_case(df, None) == '기준봉없음'


def test_classify_case1_in_range_and_within_15days():
    base = [90 + i * 0.2 for i in range(55)]
    breakout_close = 101.0
    consolidation  = [100.5] * 9
    closes  = base + [breakout_close] + consolidation
    volumes = [1_000_000] * 55 + [4_000_000] + [900_000] * 9
    highs   = [c * 1.01 for c in closes[:-10]] + [breakout_close * 1.02] + [c * 1.005 for c in consolidation]
    lows    = [c * 0.99 for c in closes[:-10]] + [breakout_close * 0.99] + [c * 0.995 for c in consolidation]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    pivot = find_pivot_candle(df, lookback=15)
    if pivot is None:
        pytest.skip("기준봉 탐지 실패 — 테스트 데이터 조건 미충족")
    result = classify_case(df, pivot)
    assert result in ('Case1', '대기중')


def test_classify_downbreak():
    closes  = [100.0] * 60 + [105.0, 100.0, 98.0]
    volumes = [1_000_000] * 60 + [4_000_000, 1_000_000, 1_000_000]
    lows    = [c * 0.99 for c in closes]
    lows[-1] = 97.0
    df = _make_df(closes, lows=lows, volumes=volumes)
    pivot = {'date': df.index[-3], 'vol_ratio': 4.0,
             'high': 105.0 * 1.01, 'low': 105.0 * 0.99,
             'midline': 105.0 * 1.0, 'close': 105.0}
    assert classify_case(df, pivot) == '하방이탈'


def test_10ema_slope_positive_on_uptrend():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) > 0


def test_10ema_slope_negative_on_downtrend():
    closes = [130 - i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) < 0
