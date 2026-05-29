import pandas as pd
import numpy as np
import pytest
from strategy.phases import detect_day1, detect_day2, PhaseResult

def make_df(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame({
        'Open': closes,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
    })

def test_detect_day1_true():
    closes = [100.0] * 25 + [92.0]
    df = make_df(closes)
    result = detect_day1(df)
    assert result is True

def test_detect_day1_false():
    closes = [100.0] * 26
    df = make_df(closes)
    result = detect_day1(df)
    assert result is False

def test_detect_day2_true():
    base_closes = [100.0] * 25
    # DAY1: bearish candle (open=100, close=92), DAY2: bullish candle (open=91.5, close=99)
    opens  = [100.0] * 25 + [100.0, 91.5]
    closes = [100.0] * 25 + [92.0,  99.0]
    highs  = [c * 1.01 for c in base_closes] + [101.0, 100.0]
    lows   = [c * 0.99 for c in base_closes] + [90.0,  91.0]
    volumes = [1_000_000] * 25 + [1_000_000, 5_000_000]
    n = len(closes)
    df = pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows,
                       'Close': closes, 'Volume': volumes})
    result = detect_day2(df, index_adr=1.5)
    assert result.is_day2 is True

def test_detect_day2_false_low_volume():
    base_closes = [100.0] * 25
    closes = base_closes + [92.0, 99.0]
    highs = [c * 1.01 for c in base_closes] + [93.0, 100.0]
    lows = [c * 0.99 for c in base_closes] + [90.0, 91.5]
    volumes = [1_000_000] * 27
    df = make_df(closes, highs, lows, volumes)
    result = detect_day2(df, index_adr=1.5)
    assert result.is_day2 is False
