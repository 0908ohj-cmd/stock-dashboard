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
    """현재 페이즈 반환: 'DAY1' | 'DAY2' | 'DAY3' ~ 'DAY5' | 'Normal'
    DAY 체계:
      DAY1 = 조정 중 (EMA21 아래, 찐반등 없거나 실패)
      DAY2 = 찐반등 감지 당일
      DAY3 = 찐반등 이후 1 거래일 (매수 유효 1일차)
      DAY4 = 찐반등 이후 2 거래일 (매수 유효 2일차)
      DAY5 = 찐반등 이후 3 거래일 (매수 유효 마지막)
      DAY5 이후 EMA21 미회복 시 DAY1 복귀
    """
    from strategy.market_status import get_market_status
    import numpy as np

    if len(df) < 25:
        return 'Normal'

    status = get_market_status(df)
    state  = status['state']

    if state == 'normal':
        return 'Normal'
    if state == 'correction':
        return 'DAY1'
    if state == 'early_signal':
        jjin_date  = status['jjin_date']
        last_date  = df.index[-1].date()
        jjin_d     = jjin_date.date() if hasattr(jjin_date, 'date') else jjin_date
        days_since = int(np.busday_count(jjin_d, last_date))
        day_num    = min(days_since + 2, 5)
        return f'DAY{max(day_num, 2)}'

    return 'Normal'
