import pandas as pd


def get_phase_label(df: pd.DataFrame, index_adr: float) -> str:
    """현재 페이즈 반환: 'DAY1' | 'DAY2' | 'DAY3' ~ 'DAY7' | 'Normal'
    DAY 체계:
      DAY1 = 조정 중 (EMA21 아래, 찐반등 없거나 실패)
      DAY2 = 찐반등 감지 당일
      DAY3 = 찐반등 이후 1 거래일 (매수 유효 1일차)
      DAY4 = 찐반등 이후 2 거래일 (매수 유효 2일차)
      DAY5 = 찐반등 이후 3 거래일 (매수 유효 3일차)
      DAY6 = 찐반등 이후 4 거래일 (매수 유효 4일차)
      DAY7 = 찐반등 이후 5 거래일 (매수 유효 마지막)
      DAY7 이후 EMA21 미회복 시 DAY1 복귀
    """
    from strategy.market_status import get_market_status
    from strategy.trading_days import trading_days_after

    if len(df) < 25:
        return 'Normal'

    status = get_market_status(df)
    state  = status['state']

    if state == 'normal':
        jjin_date = status.get('jjin_date')
        if jjin_date is not None:
            days_since = trading_days_after(df, jjin_date)
            if days_since <= 5:  # DAY7까지는 DAY 레이블 유지 (EMA21 회복 여부 무관)
                return f'DAY{min(days_since + 2, 7)}'
        return 'Normal'
    if state == 'correction':
        return 'DAY1'
    if state == 'early_signal':
        days_since = trading_days_after(df, status['jjin_date'])
        return f'DAY{min(days_since + 2, 7)}'

    return 'Normal'
