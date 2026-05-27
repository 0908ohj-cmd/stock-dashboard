import pandas as pd
from strategy.indicators import calc_ma_position


def _vol_ratio(df: pd.DataFrame) -> float:
    if len(df) < 4:
        return 0.0
    d = df.copy()
    d['is_up'] = d['Close'] >= d['Open']
    up   = d[d['is_up']]['Volume'].mean()
    down = d[~d['is_up']]['Volume'].mean()
    if pd.isna(up) or pd.isna(down) or down == 0:
        return 0.0
    return round(float(up / down), 2)


def _candle_ratio(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    bull = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open']))
        if float(r['Close']) > float(r['Open']) else 0, axis=1
    ).sum()
    bear = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open']))
        if float(r['Close']) < float(r['Open']) else 0, axis=1
    ).sum()
    if bear == 0:
        return 2.0 if bull > 0 else 1.0
    return round(float(bull / bear), 2)


def calc_correction_rs(
    stock_df: pd.DataFrame,
    index_df: pd.DataFrame,
    correction_start: pd.Timestamp,
    jjin_date: pd.Timestamp | None,
) -> dict:
    """
    조정 구간(correction_start ~ jjin_date) RS 계산.
    jjin_date가 None이면 index_df 마지막 날까지 계산.
    """
    empty = {
        'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0,
        'lead_days': 0, 'ma_score': 0,
        'vol_ratio': 0.0, 'candle_ratio': 0.0,
    }

    end = jjin_date if jjin_date is not None else index_df.index[-1]

    idx_slice = index_df[
        (index_df.index >= correction_start) & (index_df.index <= end)
    ]
    stk_slice = stock_df[
        (stock_df.index >= correction_start) & (stock_df.index <= end)
    ]

    if len(idx_slice) < 4 or len(stk_slice) < 4:
        return empty

    idx_pct = (float(idx_slice['Close'].iloc[-1]) / float(idx_slice['Close'].iloc[0]) - 1) * 100
    stk_pct = (float(stk_slice['Close'].iloc[-1]) / float(stk_slice['Close'].iloc[0]) - 1) * 100

    idx_bottom = idx_slice['Close'].idxmin()
    stk_bottom = stk_slice['Close'].idxmin()
    lead_days  = int((idx_bottom - stk_bottom).days)

    stk_for_ma = stock_df[stock_df.index <= end]
    ma_score   = calc_ma_position(stk_for_ma) if len(stk_for_ma) >= 10 else 0

    return {
        'stock_pct':    round(stk_pct, 2),
        'index_pct':    round(idx_pct, 2),
        'excess_pct':   round(stk_pct - idx_pct, 2),
        'lead_days':    lead_days,
        'ma_score':     ma_score,
        'vol_ratio':    _vol_ratio(stk_slice),
        'candle_ratio': _candle_ratio(stk_slice),
    }
