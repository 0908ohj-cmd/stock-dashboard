import pandas as pd

GRADE_ORDER = {
    'S': 0,
    'A++': 1, 'A+': 2, 'A': 3, 'A-': 4, 'A--': 5,
    'B++': 6, 'B+': 7, 'B': 8, 'B-': 9, 'B--': 10,
    'C': 11, '—': 12,
}


def _close_on(df: pd.DataFrame, date) -> float | None:
    avail = df[df.index <= pd.Timestamp(date)]
    return float(avail['Close'].iloc[-1]) if not avail.empty else None


def _weighted_grade(labels: list) -> str:
    """
    최근 날짜일수록 높은 가중치(2^i, i=0이 가장 오래된 비교).
    score = sum(고=1/저=0 * 2^i), ratio = score / max_score로 등급 결정.
    """
    n = len(labels)
    score = sum((1 if labels[i] == '고' else 0) * (2 ** i) for i in range(n))
    max_score = (2 ** n) - 1
    ratio = score / max_score if max_score > 0 else 0.0

    if ratio == 1.0: return 'S'
    if ratio >= 0.9: return 'A++'
    if ratio >= 0.8: return 'A+'
    if ratio >= 0.7: return 'A'
    if ratio >= 0.6: return 'A-'
    if ratio >= 0.5: return 'A--'
    if ratio >= 0.4: return 'B++'
    if ratio >= 0.3: return 'B+'
    if ratio >= 0.2: return 'B'
    if ratio >= 0.1: return 'B-'
    if ratio > 0.0:  return 'B--'
    return 'C'


def calc_swing_grade(stock_df: pd.DataFrame, swing_dates: list) -> dict:
    dates = sorted(set(str(d).strip() for d in swing_dates if str(d).strip()))
    if len(dates) < 2 or stock_df.empty:
        return {'grade': '—', 'pattern': ''}

    prices = [_close_on(stock_df, d) for d in dates]
    if any(p is None for p in prices):
        return {'grade': '—', 'pattern': '데이터부족'}

    labels = ['고' if prices[i] > prices[i - 1] else '저'
              for i in range(1, len(prices))]

    return {'grade': _weighted_grade(labels), 'pattern': '→'.join(labels)}
