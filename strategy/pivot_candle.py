import numpy as np
import pandas as pd
from strategy.indicators import calc_ema


def calc_10ema_slope(stock_df: pd.DataFrame, period: int = 5) -> float:
    if len(stock_df) < period + 10:
        return 0.0
    ema10 = calc_ema(stock_df, 10)
    base = float(ema10.iloc[-(period + 1)])
    if base == 0:
        return 0.0
    return float((ema10.iloc[-1] - base) / base * 100)


def _vol_ratio_at(df: pd.DataFrame, idx: int, window: int = 20) -> float:
    if idx < window:
        return 0.0
    avg = float(df['Volume'].iloc[idx - window:idx].mean())
    if avg == 0:
        return 0.0
    return float(df['Volume'].iloc[idx]) / avg


def _close_in_top30(row: pd.Series) -> bool:
    rng = float(row['High']) - float(row['Low'])
    if rng == 0:
        return False
    return (float(row['Close']) - float(row['Low'])) / rng >= 0.7


def _is_aligned(ema10: pd.Series, ema21: pd.Series, ema50: pd.Series, idx: int) -> bool:
    return float(ema10.iloc[idx]) > float(ema21.iloc[idx]) > float(ema50.iloc[idx])


def _broke_60d_high(df: pd.DataFrame, idx: int) -> bool:
    if idx < 60:
        return False
    prior_high = float(df['High'].iloc[idx - 60:idx].max())
    return float(df['Close'].iloc[idx]) > prior_high


def _broke_vcp_box(df: pd.DataFrame, idx: int,
                   min_days: int = 5, max_days: int = 20,
                   max_range_pct: float = 5.0) -> bool:
    """직전 5~20일 범위가 5% 이내(횡보 박스)이면 박스 상단 돌파 여부 반환."""
    for lookback in range(min_days, min(max_days + 1, idx)):
        window = df.iloc[idx - lookback:idx]
        hi = float(window['High'].max())
        lo = float(window['Low'].min())
        if lo == 0:
            continue
        if (hi - lo) / lo * 100 <= max_range_pct:
            box_top = float(window['Close'].max())
            if float(df['Close'].iloc[idx]) > box_top:
                return True
    return False


def find_pivot_candle(
    stock_df: pd.DataFrame,
    lookback: int = 63,
) -> dict | None:
    """
    최근 lookback 거래일 내 기준봉 탐지.
    조건: 거래량 300%+, 종가 레인지 상위 30%, 저항 돌파(60일 고점 or VCP 박스), 정배열.
    복수 후보 시 거래량비율 최고 봉 반환.
    """
    if len(stock_df) < 70:
        return None

    ema10 = calc_ema(stock_df, 10)
    ema21 = calc_ema(stock_df, 21)
    ema50 = stock_df['Close'].rolling(50).mean()

    start_idx = max(60, len(stock_df) - lookback)
    candidates = []

    for i in range(start_idx, len(stock_df) - 1):  # 오늘(마지막 봉) 제외
        vr = _vol_ratio_at(stock_df, i)
        if vr < 3.0:
            continue
        row = stock_df.iloc[i]
        if not _close_in_top30(row):
            continue
        if not (_broke_60d_high(stock_df, i) or _broke_vcp_box(stock_df, i)):
            continue
        if pd.isna(ema50.iloc[i]):
            continue
        if not _is_aligned(ema10, ema21, ema50, i):
            continue
        candidates.append((i, vr))

    if not candidates:
        return None

    best_i, best_vr = max(candidates, key=lambda x: x[1])
    row = stock_df.iloc[best_i]
    high  = float(row['High'])
    low   = float(row['Low'])
    close = float(row['Close'])
    return {
        'date':      stock_df.index[best_i],
        'vol_ratio': round(best_vr, 2),
        'high':      high,
        'low':       low,
        'midline':   round((high + low) / 2, 4),
        'close':     close,
    }


def classify_case(
    stock_df: pd.DataFrame,
    pivot: dict | None,
) -> str:
    """'기준봉없음' | '하방이탈' | '중간선이탈' | '대기중' | 'Case1' | 'Case2'"""
    if pivot is None:
        return '기준봉없음'

    current_close = float(stock_df['Close'].iloc[-1])
    current_date  = stock_df.index[-1]

    if current_close < pivot['low']:
        return '하방이탈'

    # 중간선 이탈: 기준봉 저가 위지만 중간선 아래 → 셋업 약화
    if current_close < pivot['midline']:
        return '중간선이탈'

    days_since = int(np.busday_count(pivot['date'].date(), current_date.date()))

    # Case 2: 기준봉 이후 30거래일 이내에 기준봉 고가 돌파한 적 있고 현재 복귀
    if days_since <= 30:
        since_pivot = stock_df[stock_df.index > pivot['date']]
        if not since_pivot.empty:
            ever_above = float(since_pivot['High'].max()) > pivot['high']
            back_near  = pivot['high'] * 0.97 <= current_close <= pivot['high'] * 1.05
            if ever_above and back_near:
                return 'Case2'

    # Case 1: 기준봉 midline~high 범위 횡보, 3~15거래일, 10EMA 우상향, 10EMA 위
    in_range   = pivot['midline'] <= current_close <= pivot['high'] * 1.03
    valid_days = 3 <= days_since <= 15
    slope_up   = calc_10ema_slope(stock_df) > 0
    ema10_now  = float(calc_ema(stock_df, 10).iloc[-1])
    above_ema  = current_close > ema10_now

    if in_range and valid_days and slope_up and above_ema:
        return 'Case1'

    return '대기중'
