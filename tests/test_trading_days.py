import pandas as pd
import pytest
from strategy.trading_days import trading_days_after, nth_trading_day_after


def _df_with_dates(dates):
    return pd.DataFrame({'Close': [100.0] * len(dates)}, index=pd.DatetimeIndex(dates))


# 월화수 거래 후 1주일 휴장(연휴 가정), 다음주 수목 거래
DATES = ['2026-01-05', '2026-01-06', '2026-01-07',
         '2026-01-14', '2026-01-15']


def test_trading_days_after_counts_rows_not_busdays():
    df = _df_with_dates(DATES)
    # 1/7 이후 실제 거래일은 2개 — np.busday_count로 세면 5가 나와버림
    assert trading_days_after(df, pd.Timestamp('2026-01-07')) == 2


def test_trading_days_after_zero_when_date_is_last_row():
    df = _df_with_dates(DATES)
    assert trading_days_after(df, pd.Timestamp('2026-01-15')) == 0


def test_nth_trading_day_after_skips_holidays():
    df = _df_with_dates(DATES)
    assert nth_trading_day_after(df, pd.Timestamp('2026-01-07'), 1) == pd.Timestamp('2026-01-14')
    assert nth_trading_day_after(df, pd.Timestamp('2026-01-07'), 2) == pd.Timestamp('2026-01-15')


def test_nth_trading_day_after_returns_none_when_out_of_range():
    df = _df_with_dates(DATES)
    assert nth_trading_day_after(df, pd.Timestamp('2026-01-15'), 1) is None


def test_nth_trading_day_after_rejects_nonpositive_n():
    """n=0이 조용히 마지막 거래일을 반환하면 as-of 기준일이 라이브 날짜로 둔갑한다 — 명시적 에러."""
    df = _df_with_dates(DATES)
    with pytest.raises(ValueError):
        nth_trading_day_after(df, pd.Timestamp('2026-01-05'), 0)
    with pytest.raises(ValueError):
        nth_trading_day_after(df, pd.Timestamp('2026-01-05'), -1)
