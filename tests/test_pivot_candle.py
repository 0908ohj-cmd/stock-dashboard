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
    """base_closes + breakout + today. 돌파봉은 저가를 낮게 설정해 top-30% 조건 충족.
    사전 30%+ 상승(_has_prior_move) 조건을 위해 base는 가파른 상승이어야 한다."""
    n_base = len(base_closes)
    closes  = base_closes + [breakout_close, today_close]
    highs   = [c * 1.01 for c in base_closes] + [breakout_close * 1.005, today_close * 1.01]
    lows    = [c * 0.99 for c in base_closes] + [breakout_close * 0.92, today_close * 0.99]
    volumes = [base_vol] * n_base + [breakout_vol, base_vol]
    return _make_df(closes, highs=highs, lows=lows, volumes=volumes)


# 사전 상승 조건(65거래일 내 저점 → 기준봉 고가 +30% 이상)을 충족하는 가파른 베이스
STEEP_BASE = [80 + i * 0.5 for i in range(73)]   # 80 → 116 (+45%)


def test_detects_high_volume_breakout():
    """거래량 1.5배+, 60일 고점 돌파, 정배열, 사전 30%+ 상승 → 기준봉 탐지"""
    df = _make_breakout_df(STEEP_BASE, breakout_close=120.0, today_close=120.0)
    result = find_pivot_candle(df, lookback=5)
    assert result is not None
    assert result['vol_ratio'] >= 1.5


def test_no_pivot_when_volume_insufficient():
    """거래량 1.5배 미만 → 기준봉 없음"""
    df = _make_breakout_df(STEEP_BASE, breakout_close=120.0, today_close=120.0,
                           breakout_vol=1_200_000)
    assert find_pivot_candle(df, lookback=5) is None


def test_no_pivot_when_close_not_in_top30pct():
    """종가가 레인지 하위 → 기준봉 없음 (긴 윗꼬리)"""
    closes  = STEEP_BASE + [118.0, 118.0]
    highs   = [c * 1.01 for c in STEEP_BASE] + [135.0, 119.0]   # 돌파봉에 긴 윗꼬리
    lows    = [c * 0.99 for c in STEEP_BASE] + [117.0, 117.5]
    volumes = [1_000_000] * 73 + [4_000_000, 1_000_000]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    # close=118, low=117, high=135 → (118-117)/(135-117) ≈ 0.06 < 0.7
    assert find_pivot_candle(df, lookback=5) is None


def test_picks_highest_vol_ratio_among_candidates():
    """조건 충족 후보 2개 → 거래량 비율 더 높은 것 선택"""
    base = [70 + i * 0.5 for i in range(78)]   # 70 → 108.5 (+55%)
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
    closes  = [110.0] * 40 + [105.0] * 33 + [106.0, 106.0]
    volumes = [1_000_000] * 73 + [4_000_000, 1_000_000]
    df = _make_df(closes, volumes=volumes)
    assert find_pivot_candle(df, lookback=5) is None


def test_classify_returns_no_pivot_when_none():
    df = _make_df([100.0] * 30)
    assert classify_case(df, None) == '없음'


def test_classify_setup_or_forming_in_consolidation():
    """가파른 상승 + 기준봉 + 눌림 9일 → 셋업 또는 형성중 (탈락 상태는 아님)"""
    base = [60 + i * 0.8 for i in range(62)]   # 60 → 108.8 (+81%)
    breakout_close = 112.0
    consolidation  = [111.5] * 9
    closes  = base + [breakout_close] + consolidation
    volumes = [1_000_000] * 62 + [4_000_000] + [700_000] * 9
    highs   = [c * 1.01 for c in base] + [breakout_close * 1.005] + [c * 1.005 for c in consolidation]
    lows    = [c * 0.99 for c in base] + [breakout_close * 0.93] + [c * 0.995 for c in consolidation]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    pivot = find_pivot_candle(df, lookback=15)
    assert pivot is not None, "테스트 데이터가 기준봉 탐지 조건을 충족하지 못함"
    assert classify_case(df, pivot) in ('셋업', '형성중')


def test_classify_downbreak():
    closes  = [100.0] * 60 + [105.0, 100.0, 98.0]
    volumes = [1_000_000] * 60 + [4_000_000, 1_000_000, 1_000_000]
    lows    = [c * 0.99 for c in closes]
    lows[-1] = 97.0
    df = _make_df(closes, lows=lows, volumes=volumes)
    pivot = {'date': df.index[-3], 'vol_ratio': 4.0,
             'high': 105.0 * 1.01, 'low': 105.0 * 0.99,
             'midline': 105.0 * 1.0, 'close': 105.0}
    assert classify_case(df, pivot) == '저가이탈'


def _consolidation_df(post_dates):
    """60일 상승 추세 + 기준봉(마지막 상승일) + 눌림 2봉. 눌림 봉의 날짜를 조절할 수 있다."""
    closes = [100 + i * 0.5 for i in range(60)]
    dates  = list(pd.bdate_range('2026-01-01', periods=60)) + list(post_dates)
    pivot_close = closes[-1]
    post_closes = [pivot_close - 0.3] * len(post_dates)

    all_closes = closes + post_closes
    highs   = [c * 1.001 for c in closes] + [c + 0.1 for c in post_closes]
    lows    = [c * 0.99 for c in closes] + [c - 0.5 for c in post_closes]
    volumes = [1_000_000] * 60 + [700_000] * len(post_dates)   # 눌림 거래량 수축

    df = pd.DataFrame({'Open': all_closes, 'High': highs, 'Low': lows,
                       'Close': all_closes, 'Volume': volumes},
                      index=pd.DatetimeIndex(dates))
    pivot = {'date': dates[59], 'vol_ratio': 4.0,
             'high': pivot_close + 0.5, 'low': pivot_close - 2.1,
             'midline': pivot_close - 0.8, 'close': pivot_close}
    return df, pivot


def test_classify_counts_consolidation_in_trading_days_not_busdays():
    """기준봉 뒤 실제 거래일이 2일뿐이면(연휴로 달력 영업일은 5일) 아직 셋업이 아니어야 한다."""
    pivot_date = pd.bdate_range('2026-01-01', periods=60)[-1]
    post_dates = [pivot_date + pd.Timedelta(days=6), pivot_date + pd.Timedelta(days=7)]
    df, pivot = _consolidation_df(post_dates)
    assert classify_case(df, pivot) == '형성중'


def test_classify_setup_with_three_consecutive_trading_days():
    """같은 셋업이 연속 거래일 3일이면 셋업 — 거래일 카운팅 회귀 방지."""
    pivot_date = pd.bdate_range('2026-01-01', periods=60)[-1]
    post_dates = pd.bdate_range(pivot_date + pd.Timedelta(days=1), periods=3)
    df, pivot = _consolidation_df(post_dates)
    assert classify_case(df, pivot) == '셋업'


def test_10ema_slope_positive_on_uptrend():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) > 0


def test_10ema_slope_negative_on_downtrend():
    closes = [130 - i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) < 0
