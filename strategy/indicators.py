import pandas as pd
import numpy as np


def calc_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df['Close'].ewm(span=period, adjust=False).mean()


def calc_sma(df: pd.DataFrame, period: int) -> pd.Series:
    return df['Close'].rolling(window=period).mean()


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


def find_ema_breakdown_date(index_df: pd.DataFrame, ema_period: int = 10) -> pd.Timestamp | None:
    """
    지수가 마지막으로 EMA 아래로 내려온 날짜 반환.
    RS 측정 기간 시작점으로 사용. 기본 10EMA.
    현재 지수가 EMA 위에 있으면 None 반환 (get_breakdown_slice가 fallback 20일 사용).
    """
    if len(index_df) < ema_period + 1:
        return None
    ema = calc_ema(index_df, ema_period)
    if float(index_df['Close'].iloc[-1]) >= float(ema.iloc[-1]):
        return None
    below = index_df['Close'] < ema
    transitions = below.astype(int).diff()
    breakdown_indices = transitions[transitions == 1].index
    if len(breakdown_indices) == 0:
        return None
    return breakdown_indices[-1]


def get_breakdown_slice(df: pd.DataFrame, index_df: pd.DataFrame,
                        min_days: int = 5, fallback_days: int = 20) -> pd.DataFrame:
    """
    10EMA 이탈일부터 오늘까지 슬라이스 반환.
    기간이 min_days 미만이면 최근 fallback_days 일로 대체.
    """
    breakdown_date = find_ema_breakdown_date(index_df)
    if breakdown_date is not None:
        sliced = df[df.index >= breakdown_date]
        if len(sliced) >= min_days:
            return sliced
    return df.tail(fallback_days)


# ── RS 방법 1: 단순 초과수익률 ──────────────────────────────────
def calc_rs_excess_return(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> dict:
    """
    방법1: 지수 10EMA 이탈일부터 오늘까지 수익률 차이.
    rs_ratio = 종목% - 지수%
    """
    breakdown_date = find_ema_breakdown_date(index_df)
    if breakdown_date is None:
        return {'rs_ratio': 0.0, 'stock_pct': 0.0, 'index_pct': 0.0,
                'days': 0, 'breakdown_date': 'N/A'}

    idx_slice = index_df[index_df.index >= breakdown_date]
    stk_slice = stock_df[stock_df.index >= breakdown_date]

    if len(idx_slice) < 2 or len(stk_slice) < 2:
        return {'rs_ratio': 0.0, 'stock_pct': 0.0, 'index_pct': 0.0,
                'days': 0, 'breakdown_date': str(breakdown_date.date())}

    index_pct = (float(idx_slice['Close'].iloc[-1]) / float(idx_slice['Close'].iloc[0]) - 1) * 100
    stock_pct = (float(stk_slice['Close'].iloc[-1]) / float(stk_slice['Close'].iloc[0]) - 1) * 100

    return {
        'rs_ratio': round(stock_pct - index_pct, 2),
        'stock_pct': round(stock_pct, 2),
        'index_pct': round(index_pct, 2),
        'days': len(idx_slice),
        'breakdown_date': str(breakdown_date.date()),
    }


# ── RS 방법 2: RS Line (종목/지수 비율 기울기) ─────────────────
def calc_rs_line(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> dict:
    """
    방법2: 매일 (종목종가 / 지수종가) 비율 → 최근 기울기로 강도 판단.
    기울기 양수 = 우상향 = RS 강함.
    """
    # 공통 날짜만 사용
    common_idx = stock_df.index.intersection(index_df.index)
    if len(common_idx) < 10:
        return {'slope': 0.0, 'is_uptrend': False, 'rs_line_pct': 0.0, 'rs_line_new_high': False}

    stk = stock_df.loc[common_idx, 'Close']
    idx = index_df.loc[common_idx, 'Close']

    rs_line = stk / idx

    # 최근 20일 기울기 (선형회귀)
    recent = rs_line.tail(20)
    x = np.arange(len(recent))
    slope = float(np.polyfit(x, recent.values, 1)[0])

    # RS Line이 이탈일 이후 신고가인지
    breakdown_date = find_ema_breakdown_date(index_df)
    if breakdown_date is not None:
        rs_since = rs_line[rs_line.index >= breakdown_date]
        rs_line_pct = (float(rs_since.iloc[-1]) / float(rs_since.iloc[0]) - 1) * 100 if len(rs_since) >= 2 else 0.0
    else:
        rs_line_pct = 0.0

    # RS Line 신고가 여부: 현재값이 전체 데이터 최고점의 97% 이상
    rs_line_new_high = float(rs_line.iloc[-1]) >= float(rs_line.max()) * 0.97

    return {
        'slope': round(slope * 1000, 4),   # 스케일 보정
        'is_uptrend': slope > 0,
        'rs_line_pct': round(rs_line_pct, 2),
        'rs_line_new_high': rs_line_new_high,
        'rs_line': rs_line,   # 차트용
    }


# ── RS 방법 3: IBD-style RS Rating (1~99) ─────────────────────
def calc_ibd_rs_rating(stock_df: pd.DataFrame, index_df: pd.DataFrame,
                        all_stocks_returns: list[float] | None = None) -> dict:
    """
    방법3: 63일(분기) + 126일(반기) + 189일 + 252일 가중 수익률로 순위 계산.
    all_stocks_returns: 동일 유니버스 전체 종목의 동일 지표값 리스트 (없으면 지수 대비 상대 점수)
    """
    periods = [63, 126, 189, 252]
    weights = [0.4, 0.2, 0.2, 0.2]

    score = 0.0
    for period, weight in zip(periods, weights):
        if len(stock_df) < period + 1:
            continue
        ret = (float(stock_df['Close'].iloc[-1]) / float(stock_df['Close'].iloc[-(period+1)]) - 1) * 100
        score += ret * weight

    if all_stocks_returns is not None and len(all_stocks_returns) > 1:
        # 전체 종목 중 백분위
        below = sum(1 for r in all_stocks_returns if r < score)
        rating = int(below / len(all_stocks_returns) * 99) + 1
    else:
        # 지수 대비 상대 점수로 임시 계산
        idx_score = 0.0
        for period, weight in zip(periods, weights):
            if len(index_df) < period + 1:
                continue
            ret = (float(index_df['Close'].iloc[-1]) / float(index_df['Close'].iloc[-(period+1)]) - 1) * 100
            idx_score += ret * weight
        # 지수 대비 초과분을 0~99로 정규화 (대략적)
        diff = score - idx_score
        rating = min(99, max(1, int(50 + diff * 2)))

    return {
        'ibd_score': round(score, 2),
        'ibd_rating': rating,
    }


# ── 통합: 3가지 RS 한번에 계산 ────────────────────────────────
def calc_all_rs(stock_df: pd.DataFrame, index_df: pd.DataFrame,
                all_stocks_returns: list[float] | None = None) -> dict:
    r1 = calc_rs_excess_return(stock_df, index_df)
    r2 = calc_rs_line(stock_df, index_df)
    r3 = calc_ibd_rs_rating(stock_df, index_df, all_stocks_returns)

    return {
        # 방법1: 초과수익률
        'excess_pct': r1['rs_ratio'],
        'stock_pct': r1['stock_pct'],
        'index_pct': r1['index_pct'],
        'breakdown_date': r1['breakdown_date'],
        'days': r1['days'],
        # 방법2: RS Line
        'rs_line_slope': r2['slope'],
        'rs_line_uptrend': r2['is_uptrend'],
        'rs_line_pct': r2['rs_line_pct'],
        'rs_line_new_high': r2['rs_line_new_high'],
        'rs_line_series': r2.get('rs_line'),
        # 방법3: IBD
        'ibd_score': r3['ibd_score'],
        'ibd_rating': r3['ibd_rating'],
    }
