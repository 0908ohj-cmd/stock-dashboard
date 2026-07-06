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


def _has_prior_move(df: pd.DataFrame, pivot_idx: int, pivot_high: float,
                    lookback: int = 65, min_pct: float = 30.0) -> bool:
    """기준봉 이전 lookback 거래일 내 저점 → 기준봉 고가까지 min_pct% 이상 상승 여부."""
    start = max(0, pivot_idx - lookback)
    if pivot_idx <= start:
        return False
    prior_low = float(df['Low'].iloc[start:pivot_idx].min())
    if prior_low <= 0:
        return False
    return (pivot_high / prior_low - 1) * 100 >= min_pct


def _base_is_tight(since_pivot: pd.DataFrame, pivot_high: float,
                   max_range_pct: float = 15.0) -> bool:
    """베이스 타이트함: 횡보 구간 High-Low 범위가 기준봉 고가 대비 max_range_pct 이내."""
    if since_pivot.empty:
        return True
    base_high = float(since_pivot['High'].max())
    base_low  = float(since_pivot['Low'].min())
    if pivot_high <= 0:
        return True
    return (base_high - base_low) / pivot_high * 100 <= max_range_pct


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
        if vr < 1.5:  # 쿨라매기 기준: 거래량 1.5배+ (이전 3배 기준에서 완화)
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
        # 쿨라매기 조건: 기준봉 이전 65거래일(3개월) 내 30%+ 상승 구간 존재
        if not _has_prior_move(stock_df, i, float(row['High'])):
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
    """'기준봉없음' | '하방이탈' | '중간선이탈' | '10EMA이탈' | '대기중' | 'Case1' | 'Case2'

    Case1 근거 (Minervini VCP final contraction + Qullamaggie EMA surfing):
      - 기간 3~20거래일: Minervini final VCP 5~14일, Qullamaggie 3~5주 하단
      - 가격 midline~high×1.03: Minervini final compression 3~8% 범위
      - 거래량 수축: 눌림 평균 거래량 ≤ 기준봉 이전 20일 평균 (두 트레이더 공통)
      - 10EMA 우상향 + 종가 > 10EMA: Qullamaggie EMA surfing 조건

    Case2 근거 (Qullamaggie / Minervini):
      - 기준봉 고가 위 체류 ≤ 5거래일: Qullamaggie 돌파 후 3~5일 익절 기준
      - 최대 연장 ≤ +10%: Minervini 피벗 5% 초과 추격금지 × 2
      - 현재가 기준봉 고가 -3%~+5% 복귀
    """
    if pivot is None:
        return '기준봉없음'

    current_close = float(stock_df['Close'].iloc[-1])
    current_date  = stock_df.index[-1]

    if current_close < pivot['low']:
        return '하방이탈'

    since_pivot = stock_df[stock_df.index > pivot['date']]

    # 기준봉 이후 Close가 한 번이라도 중간선 미달이면 영구 탈락
    if not since_pivot.empty and float(since_pivot['Close'].min()) < pivot['midline']:
        return '중간선이탈'
    if current_close < pivot['midline']:
        return '중간선이탈'

    # 기준봉 이후 연속 2거래일 Close < 10EMA이면 영구 탈락 (Qullamaggie: 10EMA 이탈 즉시 매도)
    if len(since_pivot) >= 2:
        ema10_since = calc_ema(stock_df, 10).loc[since_pivot.index]
        below = since_pivot['Close'] < ema10_since
        if (below & below.shift(1)).any():
            return '10EMA이탈'

    days_since = int(np.busday_count(pivot['date'].date(), current_date.date()))

    # Case2: 돌파 후 소폭 상승했다가 기준봉 고가 부근으로 복귀한 2차 매수 타점
    if days_since <= 30 and not since_pivot.empty:
        max_high        = float(since_pivot['High'].max())
        ever_above      = max_high > pivot['high']
        not_overextended = max_high <= pivot['high'] * 1.10   # Minervini 5%×2
        days_above_high = int((since_pivot['Close'] > pivot['high']).sum())
        brief_stay      = days_above_high <= 5                # Qullamaggie 3~5일
        back_near       = pivot['high'] * 0.97 <= current_close <= pivot['high'] * 1.05
        if ever_above and not_overextended and brief_stay and back_near:
            return 'Case2'

    # Case1: 기준봉 고가 아래에서 타이트하게 횡보 중인 1차 매수 타점
    in_range   = pivot['midline'] <= current_close <= pivot['high'] * 1.03
    valid_days = 3 <= days_since <= 40        # 쿨라매기 2~8주 = 최대 40거래일
    slope_up   = calc_10ema_slope(stock_df) > 0
    ema10_now  = float(calc_ema(stock_df, 10).iloc[-1])
    above_ema  = current_close > ema10_now
    base_tight = _base_is_tight(since_pivot, pivot['high'])   # 횡보 범위 ≤ 15%

    if in_range and valid_days and slope_up and above_ema and base_tight:
        pivot_pos   = stock_df.index.get_loc(pivot['date'])
        pre_vol_avg = float(stock_df['Volume'].iloc[max(0, pivot_pos - 20):pivot_pos].mean())
        if since_pivot.empty or pre_vol_avg == 0:
            vol_dry_up = True
        else:
            consol_avg = float(since_pivot['Volume'].mean())
            recent_avg = float(since_pivot['Volume'].tail(min(3, len(since_pivot))).mean())
            # 횡보 평균 거래량 ≤ 기준봉 이전 80% AND 최근 3일이 더 건조
            vol_dry_up = consol_avg <= pre_vol_avg * 0.8 and recent_avg <= consol_avg
        if vol_dry_up:
            return 'Case1'

    return '대기중'
