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


def _above_ratio(prices: list, idx: int) -> float:
    """현재가가 이전 가격들 중 몇 개보다 높은지 비율 (0.0~1.0)."""
    current = prices[idx + 1]
    prev = prices[: idx + 1]
    return sum(1 for p in prev if p < current) / len(prev)


def _calc_score(prices: list, labels: list) -> tuple[float, float]:
    """
    '고': 0.5 + 0.5 × recovery_ratio  → 기여 범위 [0.5, 1.0]
    '저': 0.5 × survival_ratio         → 기여 범위 [0.0, 0.5)

    같은 위치에서 '고'가 항상 '저'보다 높은 기여를 하므로
    저→저→고 > 저→고→저 가 성립하면서,
    '저' 내부에서도 높은 저점일수록 감점이 적다.
    max_score = 모든 위치 val=1.0 → 2^n - 1
    """
    n = len(labels)
    score = 0.0
    for i, lbl in enumerate(labels):
        ar = _above_ratio(prices, i)
        val = (0.5 + 0.5 * ar) if lbl == '고' else (0.5 * ar)
        score += val * (2 ** i)
    return score, float((2 ** n) - 1)


def _ratio_to_grade(ratio: float) -> str:
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
    if ratio > 0.0: return 'B--'
    return 'C'


def calc_swing_grade(stock_df: pd.DataFrame, swing_dates: list) -> dict:
    dates = sorted(set(str(d).strip() for d in swing_dates if str(d).strip()))
    if len(dates) < 2 or stock_df.empty:
        return {'grade': '—', 'pattern': '', 'score': 0.0}

    prices = [_close_on(stock_df, d) for d in dates]
    if any(p is None for p in prices):
        return {'grade': '—', 'pattern': '데이터부족', 'score': 0.0}

    labels = ['고' if prices[i] > prices[i - 1] else '저'
              for i in range(1, len(prices))]

    score, max_score = _calc_score(prices, labels)
    ratio = score / max_score if max_score > 0 else 0.0

    return {'grade': _ratio_to_grade(ratio), 'pattern': '→'.join(labels), 'score': ratio}
