import pandas as pd
import numpy as np
from strategy.scoring import score_volume_asymmetry, score_candle_ratio, total_score_with_detail

def make_df(opens, closes, volumes=None, highs=None, lows=None):
    n = len(opens)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [max(o, c) * 1.01 for o, c in zip(opens, closes)]
    if lows is None:
        lows = [min(o, c) * 0.99 for o, c in zip(opens, closes)]
    return pd.DataFrame({
        'Open': opens,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
    })

def make_flat_index(n=20, price=100.0):
    """Constant price index — no EMA breakdown, forces fallback slice in get_breakdown_slice."""
    return pd.DataFrame({
        'Open': [price] * n,
        'High': [price * 1.005] * n,
        'Low': [price * 0.995] * n,
        'Close': [price] * n,
        'Volume': [1_000_000] * n
    })

def test_score_volume_asymmetry_good():
    # Up days with high volume, down days with low volume
    opens  = [100, 102, 98, 101, 97]
    closes = [102, 100, 101, 99, 100]
    volumes = [2_000_000, 300_000, 1_800_000, 400_000, 1_500_000]
    stock = make_df(opens, closes, volumes)
    index = make_flat_index()
    score, detail = score_volume_asymmetry(stock, index)
    assert score == 1

def test_score_volume_asymmetry_bad():
    # Down days with high volume, up days with low volume
    opens  = [100, 102, 98, 101, 97]
    closes = [102, 100, 101, 99, 100]
    volumes = [300_000, 2_000_000, 400_000, 1_800_000, 500_000]
    stock = make_df(opens, closes, volumes)
    index = make_flat_index()
    score, detail = score_volume_asymmetry(stock, index)
    assert score == 0

def test_score_candle_ratio_good():
    # Bull candle sum > bear candle sum
    opens  = [100, 102, 98]
    closes = [98, 100, 104]   # -2, -2, +6 → bull=6 bear=4
    stock = make_df(opens, closes)
    index = make_flat_index()
    score, detail = score_candle_ratio(stock, index)
    assert score == 1

def test_score_candle_ratio_bad():
    # Bear candle sum > bull candle sum
    opens  = [100, 98, 103]
    closes = [95, 101, 100]   # -5, +3, -3 → bull=3 bear=8
    stock = make_df(opens, closes)
    index = make_flat_index()
    score, detail = score_candle_ratio(stock, index)
    assert score == 0

def test_total_score_range():
    opens  = [100, 102, 98, 101, 97]
    closes = [102, 100, 101, 99, 103]
    volumes = [2_000_000, 300_000, 1_800_000, 400_000, 2_500_000]
    stock = make_df(opens, closes, volumes)
    index = make_flat_index()
    result = total_score_with_detail(stock, index)
    assert 0 <= result['score'] <= 3
