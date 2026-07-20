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
    """
    현재가(prices[idx+1])가 이전 가격들(prices[0..idx]) 중 몇 개보다 높은지 비율 (0.0~1.0).

    '고': 직전가 반드시 포함 → minimum 1/(idx+1)
    '저': 직전가 미포함    → maximum idx/(idx+1)

    예) 저(500)→고(800)→저(700): idx=1, current=700, prev=[500,800]
        count([500,800] < 700) / 2 = 1/2 = 0.5  ← 절반은 위에 있음 (높은 저점)

    예) 저(500)→고(800)→저(400): idx=1, current=400, prev=[500,800]
        count([500,800] < 400) / 2 = 0/2 = 0.0  ← 전부 위에 없음 (새 절대 저점)
    """
    current = prices[idx + 1]
    prev = prices[: idx + 1]
    return sum(1 for p in prev if p < current) / len(prev)


def _calc_score(prices: list, labels: list) -> tuple[float, float]:
    """
    모든 위치(고/저 공통)에 above_ratio × 2^i 적용.
    - S (모든 고, 완전 돌파) = max_score
    - C (모든 저, 전부 새 절대 저점) = 0
    """
    n = len(labels)
    score = sum(_above_ratio(prices, i) * (2 ** i) for i in range(n))
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
        return {'grade': '—', 'pattern': ''}

    prices = [_close_on(stock_df, d) for d in dates]
    if any(p is None for p in prices):
        return {'grade': '—', 'pattern': '데이터부족'}

    labels = ['고' if prices[i] > prices[i - 1] else '저'
              for i in range(1, len(prices))]

    score, max_score = _calc_score(prices, labels)
    ratio = score / max_score if max_score > 0 else 0.0

    return {'grade': _ratio_to_grade(ratio), 'pattern': '→'.join(labels)}
