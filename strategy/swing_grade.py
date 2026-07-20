import pandas as pd

GRADE_ORDER = {
    'S': 0,
    'A++': 1, 'A+': 2, 'A': 3, 'A-': 4, 'A--': 5,
    'B++': 6, 'B+': 7, 'B': 8, 'B-': 9, 'B--': 10,
    'C': 11, '—': 12,
}

_GRADES_ASC = ['B--', 'B-', 'B', 'B+', 'B++', 'A--', 'A-', 'A', 'A+', 'A++']


def _close_on(df: pd.DataFrame, date) -> float | None:
    avail = df[df.index <= pd.Timestamp(date)]
    return float(avail['Close'].iloc[-1]) if not avail.empty else None


def _above_ratio(prices: list, idx: int) -> float:
    """현재가가 이전 가격들 중 몇 개보다 높은지 비율 (0.0~1.0)."""
    current = prices[idx + 1]
    prev = prices[: idx + 1]
    return sum(1 for p in prev if p < current) / len(prev)


def _binary_score(labels: list) -> int:
    """
    이진 재귀 가중 점수 (최근일수록 2^i 높은 가중치).
    고=1, 저=0 → 0 ~ 2^n-1 사이 정수.
    서로 다른 패턴은 항상 다른 점수를 가진다.
    """
    return sum((1 if lbl == '고' else 0) * (2 ** i) for i, lbl in enumerate(labels))


def _grade_from_binary(b_score: int, max_score: int) -> str:
    """
    이진 점수 → 등급 레이블.
    N개 날짜의 경우의 수(2^(N-1))를 기반으로 동적 분할 — 하드코딩 없음.
    """
    if b_score == max_score:
        return 'S'
    if b_score == 0:
        return 'C'
    # 중간 구간 [1, max_score-1] 을 _GRADES_ASC 10단계로 균등 분할
    rank = b_score - 1           # 0 ~ max_score-2
    total = max_score - 1        # 경우의 수 - 2 (S, C 제외)
    idx = min(int(rank * 10 / total), 9)
    return _GRADES_ASC[idx]


def _sub_score(prices: list, n: int, max_binary: int) -> float:
    """
    같은 등급(같은 이진 패턴) 내에서 가격 수준으로 세분화하는 연속 점수.
    above_ratio × 2^i 합산 후 정규화 → [0, 1).
    """
    raw = sum(_above_ratio(prices, i) * (2 ** i) for i in range(n))
    return raw / max_binary if max_binary > 0 else 0.0


def calc_swing_grade(stock_df: pd.DataFrame, swing_dates: list) -> dict:
    dates = sorted(set(str(d).strip() for d in swing_dates if str(d).strip()))
    if len(dates) < 2 or stock_df.empty:
        return {'grade': '—', 'pattern': '', 'score': 0.0}

    prices = [_close_on(stock_df, d) for d in dates]
    if any(p is None for p in prices):
        return {'grade': '—', 'pattern': '데이터부족', 'score': 0.0}

    n = len(dates) - 1
    labels = ['고' if prices[i] > prices[i - 1] else '저' for i in range(1, len(prices))]

    max_binary = (2 ** n) - 1
    b_score = _binary_score(labels)

    return {
        'grade':   _grade_from_binary(b_score, max_binary),
        'pattern': '→'.join(labels),
        'score':   _sub_score(prices, n, max_binary),
    }
