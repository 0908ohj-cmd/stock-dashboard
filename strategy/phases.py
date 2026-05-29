from dataclasses import dataclass
import pandas as pd
from strategy.indicators import calc_ema, calc_adr


@dataclass
class PhaseResult:
    is_day2: bool
    day1_candle_size: float
    day2_candle_size: float
    volume_ratio: float
    price_move_pct: float


def detect_day1(df: pd.DataFrame) -> bool:
    """최근 봉이 21EMA 아래로 내려갔는지 확인."""
    if len(df) < 22:
        return False
    ema21 = calc_ema(df, period=21)
    return float(df['Close'].iloc[-1]) < float(ema21.iloc[-1])


def detect_day2(df: pd.DataFrame, index_adr: float) -> PhaseResult:
    """
    마지막 2개 봉 기준으로 DAY1→DAY2 패턴 감지.
    조건: 이전봉 < EMA21, 현재봉 거래량 급증(1.5x) + 상승폭 >= index_adr + 양봉 >= DAY1 크기 x 0.8
    """
    if len(df) < 25:
        return PhaseResult(False, 0.0, 0.0, 0.0, 0.0)

    ema21 = calc_ema(df, period=21)
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    day1_below_ema = float(prev['Close']) < float(ema21.iloc[-2])

    avg_vol = float(df['Volume'].iloc[-22:-2].mean())
    vol_ratio = float(curr['Volume']) / avg_vol if avg_vol > 0 else 0.0
    high_volume = vol_ratio >= 1.5

    price_move_pct = (float(curr['Close']) - float(curr['Open'])) / float(curr['Open']) * 100
    big_move = price_move_pct >= index_adr

    day1_size = abs(float(prev['Close']) - float(prev['Open']))
    day2_size = abs(float(curr['Close']) - float(curr['Open']))
    candle_match = day2_size >= day1_size * 0.8 and float(curr['Close']) > float(curr['Open'])

    is_day2 = day1_below_ema and high_volume and big_move and candle_match

    return PhaseResult(
        is_day2=is_day2,
        day1_candle_size=day1_size,
        day2_candle_size=day2_size,
        volume_ratio=vol_ratio,
        price_move_pct=price_move_pct,
    )


def get_phase_label(df: pd.DataFrame, index_adr: float) -> str:
    """현재 페이즈 반환: 'DAY1' | 'DAY2' | 'DAY3' ~ 'DAY5' | 'Normal'"""
    if len(df) < 25:
        return 'Normal'
    if detect_day1(df):
        return 'DAY1'
    result = detect_day2(df, index_adr)
    if result.is_day2:
        return 'DAY2'
    for lookback in range(2, 6):
        if len(df) < lookback + 25:
            break
        sub = df.iloc[:-lookback]
        if detect_day2(sub, index_adr).is_day2:
            day2_low = float(df.iloc[-(lookback + 1)]['Low'])
            day2_high = float(df.iloc[-(lookback + 1)]['High'])
            curr_low = float(df.iloc[-1]['Low'])
            curr_close = float(df.iloc[-1]['Close'])
            if curr_low >= day2_low or curr_close >= day2_high:
                return f'DAY{lookback + 1}'
    return 'Normal'
