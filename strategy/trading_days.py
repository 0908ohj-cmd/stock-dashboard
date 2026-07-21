"""실제 거래일(데이터 행) 기반 날짜 계산.

np.busday_count는 한국 공휴일·임시휴장을 몰라서 연휴가 끼면 DAY 카운팅이
어긋난다. OHLCV DataFrame의 인덱스 자체가 실제 거래일 달력이므로 그걸 쓴다.
"""
import pandas as pd


def trading_days_after(df: pd.DataFrame, date) -> int:
    """date 이후(미포함) 실제 거래일 수."""
    if df.empty:
        return 0
    return int((df.index > pd.Timestamp(date)).sum())


def nth_trading_day_after(df: pd.DataFrame, date, n: int) -> pd.Timestamp | None:
    """date 이후 n번째 거래일. 데이터가 거기까지 없으면 None. n >= 1만 허용
    (n<=0을 허용하면 음수 인덱싱으로 마지막 거래일이 조용히 반환된다)."""
    if n < 1:
        raise ValueError(f'n은 1 이상이어야 함: {n}')
    after = df.index[df.index > pd.Timestamp(date)]
    if len(after) < n:
        return None
    return after[n - 1]
