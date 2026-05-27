import pandas as pd
import pytest
from strategy.rs_correction import calc_correction_rs


def _make_df(closes, opens=None, volumes=None, start='2026-01-01'):
    n       = len(closes)
    dates   = pd.date_range(start, periods=n, freq='B')
    opens   = opens   or closes[:]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        'Open':   opens,
        'High':   [c * 1.01 for c in closes],
        'Low':    [c * 0.99 for c in closes],
        'Close':  closes,
        'Volume': volumes,
    }, index=dates)


def test_excess_pct_positive_when_stock_outperforms():
    dates      = pd.date_range('2026-01-01', periods=20, freq='B')
    idx_df     = _make_df([100 - i for i in range(20)])       # -19%
    stk_df     = _make_df([100 - i * 0.25 for i in range(20)]) # -4.75%
    result     = calc_correction_rs(
        stk_df, idx_df,
        correction_start=dates[0],
        jjin_date=dates[-1],
    )
    assert result['excess_pct'] > 0
    assert result['stock_pct'] > result['index_pct']


def test_lead_days_positive_when_stock_bottoms_first():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    # 지수: 20일 하락 후 10일 반등
    idx_closes = [100 - i * 2 for i in range(20)] + [60 + i * 2 for i in range(10)]
    # 종목: 10일 하락 후 20일 반등  (10일 먼저 저점)
    stk_closes = [100 - i * 4 for i in range(10)] + [60 + i * 2 for i in range(20)]
    result = calc_correction_rs(
        _make_df(stk_closes), _make_df(idx_closes),
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['lead_days'] > 0


def test_lead_days_negative_when_stock_bottoms_later():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    idx_closes = [100 - i * 4 for i in range(10)] + [60 + i * 2 for i in range(20)]
    stk_closes = [100 - i * 2 for i in range(20)] + [60 + i * 2 for i in range(10)]
    result = calc_correction_rs(
        _make_df(stk_closes), _make_df(idx_closes),
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['lead_days'] < 0


def test_returns_zeros_on_insufficient_data():
    dates  = pd.date_range('2026-01-01', periods=3, freq='B')
    idx_df = _make_df([100, 98, 96])
    stk_df = _make_df([50, 49, 48])
    result = calc_correction_rs(
        stk_df, idx_df,
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['excess_pct'] == 0.0
    assert result['lead_days']  == 0
