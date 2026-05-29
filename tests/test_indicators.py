import pandas as pd
import numpy as np
from strategy.indicators import calc_ema, calc_sma, calc_adr, calc_rs_excess_return

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

def test_calc_sma_constant_series():
    df = make_ohlcv([50.0] * 210)
    result = calc_sma(df, period=200)
    assert abs(result.iloc[-1] - 50.0) < 0.01

def test_calc_adr():
    closes = [100.0] * 20
    highs = [102.0] * 20
    lows = [98.0] * 20
    df = make_ohlcv(closes, highs, lows)
    adr = calc_adr(df, period=14)
    assert abs(adr - 4.0) < 0.01

def test_calc_rs_excess_return_no_breakdown():
    # Flat index → no EMA breakdown → rs_ratio defaults to 0.0
    stock = make_ohlcv([100.0] * 30)
    index = make_ohlcv([100.0] * 30)
    result = calc_rs_excess_return(stock, index)
    assert result['rs_ratio'] == 0.0

def make_ohlcv_dt(closes, highs=None, lows=None):
    n = len(closes)
    dates = pd.date_range('2024-01-01', periods=n, freq='B')
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    return pd.DataFrame({
        'Open': closes, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': [1_000_000] * n
    }, index=dates)

def test_calc_rs_excess_return_outperform():
    # Index breaks below EMA10, then stock recovers more than index
    index_closes = [100.0] * 20 + [90.0] * 10
    index_df = make_ohlcv_dt(index_closes)
    stock_closes = [100.0] * 20 + [88.0, 89.0, 91.0, 93.0, 95.0, 97.0, 98.0, 99.0, 100.0, 105.0]
    stock_df = make_ohlcv_dt(stock_closes)
    result = calc_rs_excess_return(stock_df, index_df)
    assert result['rs_ratio'] > 0
