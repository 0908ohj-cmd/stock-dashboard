import pandas as pd
from strategy.indicators import calc_ema, calc_adr

def make_ohlcv(closes, highs=None, lows=None):
    n = len(closes)
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    return pd.DataFrame({
        'Open': closes,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': [1_000_000] * n
    })

def test_calc_ema_length():
    df = make_ohlcv([100.0] * 30)
    result = calc_ema(df, period=21)
    assert len(result) == len(df)

def test_calc_ema_constant_series():
    df = make_ohlcv([100.0] * 30)
    result = calc_ema(df, period=21)
    assert abs(result.iloc[-1] - 100.0) < 0.01

def test_calc_adr():
    closes = [100.0] * 20
    highs = [102.0] * 20
    lows = [98.0] * 20
    df = make_ohlcv(closes, highs, lows)
    adr = calc_adr(df, period=14)
    assert abs(adr - 4.0) < 0.01
