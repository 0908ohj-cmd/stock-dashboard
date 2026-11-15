import pandas as pd


def calc_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df['Close'].ewm(span=period, adjust=False).mean()


def calc_ma_position(df: pd.DataFrame) -> int:
    """
    현재 종가가 10EMA/21EMA/50SMA/150SMA/200SMA 위에 있는 개수 반환 (0~5).
    미너비니 기준: 지수가 망가졌을 때 높은 점수 = 차기 주도주 후보.
    """
    if len(df) < 10:
        return 0

    close = float(df['Close'].iloc[-1])
    mas = [
        calc_ema(df, 10).iloc[-1],
        calc_ema(df, 21).iloc[-1],
        df['Close'].rolling(50).mean().iloc[-1],
        df['Close'].rolling(150).mean().iloc[-1],
        df['Close'].rolling(200).mean().iloc[-1],
    ]
    return sum(1 for v in mas if not pd.isna(v) and close > v)


def calc_pct_from_52w_high(df: pd.DataFrame) -> float:
    """52주(252일) 고점 대비 현재 낙폭%. 음수일수록 많이 빠진 것."""
    window = df['High'].tail(252)
    if window.empty:
        return 0.0
    high_52w = float(window.max())
    close = float(df['Close'].iloc[-1])
    return round((close / high_52w - 1) * 100, 1)


def calc_adr(df: pd.DataFrame, period: int = 14) -> float:
    daily_range_pct = (df['High'] - df['Low']) / df['Close'] * 100
    return float(daily_range_pct.tail(period).mean())
