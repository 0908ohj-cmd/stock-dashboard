import numpy as np
import pandas as pd
from strategy.indicators import calc_ma_position, calc_ema


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


def _pre_adr(stock_df: pd.DataFrame, correction_start: pd.Timestamp, period: int = 14) -> float:
    pre = stock_df[stock_df.index < correction_start].tail(period)
    if len(pre) < 2:
        return 0.0
    return float(((pre['High'] - pre['Low']) / pre['Close'] * 100).mean())


def _index_peak_date(index_df: pd.DataFrame, correction_start: pd.Timestamp, lookback: int = 63) -> pd.Timestamp:
    """
    현재 조정 시작 전 마지막 EMA21 회복일 이후의 최고점.
    직전 회복일을 찾지 못하면 lookback일 fallback.
    """
    ema21 = calc_ema(index_df, 21)
    below = (index_df['Close'] < ema21).astype(int)
    # -1 전환 = 아래→위 (EMA21 회복)
    recoveries = below.diff()[below.diff() == -1].index
    recs_before = recoveries[recoveries < correction_start]

    if len(recs_before) > 0:
        last_recovery = recs_before[-1]
        window = index_df[(index_df.index >= last_recovery) & (index_df.index <= correction_start)]
    else:
        window = index_df[index_df.index <= correction_start].tail(lookback)

    return window['Close'].idxmax()


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
        'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0, 'excess_adr': 0.0,
        'lead_days': 0, 'ma_score': 0,
        'vol_ratio': 0.0, 'candle_ratio': 0.0,
    }

    end        = jjin_date if jjin_date is not None else index_df.index[-1]
    peak_date  = _index_peak_date(index_df, correction_start)

    # RS%, 저점선행 모두 고점부터 측정
    idx_slice = index_df[(index_df.index >= peak_date) & (index_df.index <= end)]
    stk_slice = stock_df[(stock_df.index >= peak_date) & (stock_df.index <= end)]

    if len(idx_slice) < 2 or len(stk_slice) < 2:
        return empty

    idx_pct = (float(idx_slice['Close'].iloc[-1]) / float(idx_slice['Close'].iloc[0]) - 1) * 100
    stk_pct = (float(stk_slice['Close'].iloc[-1]) / float(stk_slice['Close'].iloc[0]) - 1) * 100

    idx_bottom = idx_slice['Low'].idxmin()
    stk_bottom = stk_slice['Low'].idxmin()
    lead_days  = int(np.busday_count(stk_bottom.date(), idx_bottom.date()))

    stk_for_ma = stock_df[stock_df.index <= end]
    ma_score   = calc_ma_position(stk_for_ma) if len(stk_for_ma) >= 10 else 0

    excess_pct = round(stk_pct - idx_pct, 2)
    adr        = _pre_adr(stock_df, correction_start)
    excess_adr = round(excess_pct / adr, 2) if adr > 0 else 0.0

    return {
        'stock_pct':    round(stk_pct, 2),
        'index_pct':    round(idx_pct, 2),
        'excess_pct':   excess_pct,
        'excess_adr':   excess_adr,
        'lead_days':    lead_days,
        'ma_score':     ma_score,
        'vol_ratio':    _vol_ratio(stk_slice),
        'candle_ratio': _candle_ratio(stk_slice),
    }
