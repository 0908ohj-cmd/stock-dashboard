import pandas as pd
import numpy as np
import pytest
from strategy.market_status import detect_jjin_bounce, get_market_status


def _make_df(closes, opens=None, highs=None, lows=None, volumes=None):
    n = len(closes)
    dates = pd.date_range('2026-01-01', periods=n, freq='B')
    opens = opens or closes[:]
    highs = highs or [c * 1.01 for c in closes]
    lows  = lows  or [c * 0.99 for c in closes]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame(
        {'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes},
        index=dates,
    )


def _correction_df():
    """
    25일 상승(100→124) → 10일 하락(음봉, EMA21 이탈) → 1일 찐반등.
    반등 시 EMA21≈110 > close=105 보장.
    """
    up_c  = [100 + i for i in range(25)]
    up_o  = [100 + i for i in range(25)]
    # 10일 하락: open > close (음봉), body=3
    down_c = [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]
    down_o = [127, 124, 121, 118, 115, 112, 109, 106, 103, 100]
    # 반등: close=105, open=97, body=8, prev_body=3 → cover=267%
    bounce_c, bounce_o = 105, 97
    closes  = up_c  + down_c  + [bounce_c]
    opens   = up_o  + down_o  + [bounce_o]
    highs   = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    return _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)


def test_detect_jjin_bounce_returns_none_on_plain_downtrend():
    closes = [100 - i for i in range(30)]
    df = _make_df(closes)
    assert detect_jjin_bounce(df) is None


def test_detect_jjin_bounce_detects_adr_covering_candle():
    df = _correction_df()
    result = detect_jjin_bounce(df)
    assert result is not None
    assert result['pct'] > 0
    assert result['cover_pct'] >= 70


def test_detect_jjin_bounce_gap_up_qualifies():
    """갭업도 조건 충족 시 인정"""
    up_c  = [100 + i for i in range(25)]
    up_o  = [100 + i for i in range(25)]
    down_c = [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]
    down_o = [127, 124, 121, 118, 115, 112, 109, 106, 103, 100]
    # 갭업: close=108, open=97, body=11 > prev_body(3)*0.7
    gap_c, gap_o = 108, 97
    closes  = up_c  + down_c  + [gap_c]
    opens   = up_o  + down_o  + [gap_o]
    highs   = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    df = _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    assert detect_jjin_bounce(df) is not None


def test_detect_jjin_bounce_fails_when_body_coverage_below_70pct():
    up_c  = [100 + i for i in range(25)]
    up_o  = [100 + i for i in range(25)]
    down_c = [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]
    down_o = [127, 124, 121, 118, 115, 112, 109, 106, 103, 100]
    # 바디=2 < 이전음봉바디(3)*0.7=2.1 → 커버 부족
    bounce_c, bounce_o = 99, 97
    closes  = up_c  + down_c  + [bounce_c]
    opens   = up_o  + down_o  + [bounce_o]
    highs   = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    df = _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    assert detect_jjin_bounce(df) is None


def test_get_market_status_normal():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    status = get_market_status(df)
    assert status['state'] == 'normal'


def test_get_market_status_correction():
    closes = [100 + i for i in range(25)] + [80] * 5
    df = _make_df(closes)
    status = get_market_status(df)
    assert status['state'] == 'correction'
    assert status['correction_start'] is not None


def test_get_market_status_early_signal():
    df = _correction_df()
    status = get_market_status(df)
    assert status['state'] in ('early_signal', 'ftd_confirmed')
    assert status['jjin_date'] is not None
    assert status['jjin_pct'] > 0


def test_jjin_bounce_skips_incomplete_ohlc_row():
    """Close만 있고 OHL이 NaN인 불완전 행(지수 패치 잔재 등)을 찐반등으로 오검출하면 안 된다."""
    dates = pd.date_range('2026-01-01', periods=35, freq='B')
    opens  = [100.0] * 30 + [100.0, 98.0, 96.0, 94.0] + [np.nan]
    closes = [100.0] * 30 + [98.0, 96.0, 94.0, 92.0]  + [96.7]   # 마지막 행 +5.1%
    highs  = [101.0] * 30 + [100.5, 98.5, 96.5, 94.5] + [np.nan]
    lows   = [99.0]  * 30 + [97.5, 95.5, 93.5, 91.5]  + [np.nan]
    vols   = [1_000_000] * 34 + [1_300_000]
    df = pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows,
                       'Close': closes, 'Volume': vols}, index=dates)

    assert detect_jjin_bounce(df) is None
