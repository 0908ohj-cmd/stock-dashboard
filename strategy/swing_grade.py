import itertools
from functools import lru_cache

import pandas as pd

GRADE_ORDER = {
    'S': 0,
    'A++': 1, 'A+': 2, 'A': 3, 'A-': 4, 'A--': 5,
    'B++': 6, 'B+': 7, 'B': 8, 'B-': 9, 'B--': 10,
    'C': 11, 'F': 12, '—': 13,
}

_GRADES_ASC = ['B--', 'B-', 'B', 'B+', 'B++', 'A--', 'A-', 'A', 'A+', 'A++']


def _low_on(df: pd.DataFrame, date) -> float | None:
    """해당 날짜 이전 마지막 거래일의 저가(Low) 반환."""
    avail = df[df.index <= pd.Timestamp(date)]
    return float(avail['Low'].iloc[-1]) if not avail.empty else None


def _label_value(current: float, previous: float, seq_min: float, seq_max: float) -> int:
    """
    신고(3): 구간 내 절대 최고가 갱신
    고  (2): 이전가 대비 상승, 최고가 미갱신
    저  (1): 이전가 대비 하락, 최저가 미갱신
    신저(0): 구간 내 절대 최저가 갱신
    """
    if current > seq_max:
        return 3
    elif current > previous:
        return 2
    elif current >= seq_min:
        return 1
    else:
        return 0


def _label_str(v: int) -> str:
    return {3: '신고', 2: '고', 1: '저', 0: '신저'}[v]


def _seq_score(label_values: list) -> int:
    """최근 구간일수록 4^i 높은 가중치 (신고=3, 고=2, 저=1, 신저=0)."""
    return sum(v * (4 ** i) for i, v in enumerate(label_values))


def _max_seq_score(n: int) -> int:
    """전구간 신고(3) 패턴의 점수 = 4^n - 1."""
    return (4 ** n) - 1


@lru_cache(maxsize=8)
def _achievable_intermediate_scores(n: int) -> tuple:
    """
    n개 구간에서 달성 가능한 중간 점수 집합 (S·C 제외).
    i=0 구간: 신고(3) 또는 신저(0)만 가능.
    i>0 구간: 신고/고/저/신저 모두 가능.
    """
    max_s = _max_seq_score(n)
    scores: set[int] = set()
    for first in (0, 3):
        for rest in itertools.product((0, 1, 2, 3), repeat=max(n - 1, 0)):
            s = _seq_score([first] + list(rest))
            if 0 < s < max_s:
                scores.add(s)
    return tuple(sorted(scores))


def _grade_from_score(t_score: int, n: int) -> str:
    max_s = _max_seq_score(n)
    if t_score == max_s:
        return 'S'
    if t_score == 0:
        return 'C'
    intermediates = _achievable_intermediate_scores(n)
    if t_score not in intermediates:
        return '—'
    rank = intermediates.index(t_score)
    total = len(intermediates)
    idx = min(int(rank * 10 / total), 9)
    return _GRADES_ASC[idx]


def _sub_score(prices: list, n: int) -> float:
    """
    같은 등급(패턴) 내 가격 수준 세분화.
    current / max(이전 저가들) 비율의 2^i 가중합.
    높을수록 이전 고점 대비 더 높이 회복한 것.
    """
    total = 0.0
    weight_sum = 0.0
    for i in range(n):
        current = prices[i + 1]
        prev_max = max(prices[:i + 1])
        total += (current / prev_max) * (2 ** i)
        weight_sum += 2 ** i
    return total / weight_sum if weight_sum > 0 else 0.0


def calc_swing_grade(stock_df: pd.DataFrame, swing_dates: list) -> dict:
    dates = sorted(set(str(d).strip() for d in swing_dates if str(d).strip()))
    if len(dates) < 2 or stock_df.empty:
        return {'grade': '—', 'pattern': '', 'score': 0.0}

    prices = [_low_on(stock_df, d) for d in dates]
    if any(p is None for p in prices):
        return {'grade': '—', 'pattern': '데이터부족', 'score': 0.0}

    n = len(dates) - 1
    label_values: list[int] = []
    seq_min = prices[0]
    seq_max = prices[0]
    for i in range(n):
        lv = _label_value(prices[i + 1], prices[i], seq_min, seq_max)
        label_values.append(lv)
        seq_min = min(seq_min, prices[i + 1])
        seq_max = max(seq_max, prices[i + 1])

    t_score = _seq_score(label_values)

    return {
        'grade':   _grade_from_score(t_score, n),
        'pattern': '→'.join(_label_str(v) for v in label_values),
        'score':   _sub_score(prices, n),
    }
