import pandas as pd
from strategy.phases import get_phase_label


def test_day_count_uses_trading_days_after_holiday_gap():
    """찐반등 다음 거래일이 연휴(7일 뒤)라면 DAY3이어야 한다. busday로 세면 DAY5가 돼버림."""
    # 30일 횡보 → 5일 하락(EMA21 이탈) → 찐반등 → 7일 뒤(연휴 후) 첫 거래일
    dates = list(pd.bdate_range('2026-01-01', periods=36))
    final_date = dates[-1] + pd.Timedelta(days=7)   # 같은 요일 → 달력 영업일로는 5일 차이
    all_dates = dates + [final_date]

    opens  = [100.0] * 30 + [100.0, 98.0, 96.0, 94.0, 92.0] + [90.0, 94.0]
    closes = [100.0] * 30 + [98.0, 96.0, 94.0, 92.0, 90.0]  + [94.0, 94.5]
    highs  = [101.0] * 30 + [o + 0.5 for o in [100.0, 98.0, 96.0, 94.0, 92.0]] + [94.5, 95.0]
    lows   = [99.0]  * 30 + [c - 0.5 for c in [98.0, 96.0, 94.0, 92.0, 90.0]]  + [89.0, 93.5]
    volumes = [1_000_000] * 35 + [1_300_000, 1_000_000]

    df = pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows,
                       'Close': closes, 'Volume': volumes},
                      index=pd.DatetimeIndex(all_dates))

    assert get_phase_label(df, index_adr=2.0) == 'DAY3'
