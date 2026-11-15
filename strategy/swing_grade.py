from bisect import bisect_right
from functools import lru_cache

import pandas as pd

GRADE_ORDER = {
    'S': 0,
    'A++': 1, 'A+': 2, 'A': 3, 'A-': 4, 'A--': 5,
    'B++': 6, 'B+': 7, 'B': 8, 'B-': 9, 'B--': 10,
    'C': 11, 'F': 12, '—': 13,
}

_GRADES_ASC = ['B--', 'B-', 'B', 'B+', 'B++', 'A--', 'A-', 'A', 'A+', 'A++']

# 레이블 전이 — 상태 = 직전 저가가 지금까지 구간의 최저·최고와 어떤 관계인가.
# 같은 저가(=고, #40 M4)가 들어오면 다음 레이블 가능 여부가 직전 레이블만으로 정해지지 않는다
# (첫 구간 같은 저가 '고' 직후엔 '저'가 불가능하지만, 최저보다 높은 '고' 직후엔 가능하다) → 상태로 추적.
#   'flat': 최저 = 최고 = 직전   (첫 날짜, 또는 같은 저가만 이어짐)
#   'top' : 직전 = 최고 > 최저
#   'bot' : 직전 = 최저 < 최고
#   'mid' : 최저 < 직전 < 최고
# 항목: (레이블, 다음 상태, 서로 다른 저가만으로도 가능한가 — 등급 눈금용)
_TRANSITIONS: dict[str, tuple[tuple[int, str, bool], ...]] = {
    'flat': ((3, 'top', True), (2, 'flat', False), (0, 'bot', True)),
    'top':  ((3, 'top', True), (2, 'top', False),                      # 같은 저가 = 최고
             (1, 'mid', True), (1, 'bot', False), (0, 'bot', True)),
    'bot':  ((3, 'top', True), (2, 'top', False), (2, 'bot', False),   # 쌍바닥
             (2, 'mid', True), (0, 'bot', True)),
    'mid':  ((3, 'top', True), (2, 'top', False), (2, 'mid', True),
             (1, 'mid', True), (1, 'bot', False), (0, 'bot', True)),
}


def _trading_day(df: pd.DataFrame, date) -> pd.Timestamp | None:
    """해당 날짜 이하 마지막 거래일 (주말·휴장일 → 직전 거래일). 데이터보다 앞이면 None."""
    avail = df.index[df.index <= pd.Timestamp(date)]
    return avail[-1] if len(avail) else None


def _label_value(current: float, previous: float, seq_min: float, seq_max: float) -> int:
    """
    신고(3): 구간 내 절대 최고가 갱신
    고  (2): 이전가 이상(같은 저가 = 쌍바닥 = 지지 확인, #40 M4), 최고가 미갱신
    저  (1): 이전가 대비 하락, 최저가 미갱신
    신저(0): 구간 내 절대 최저가 갱신
    """
    if current > seq_max:
        return 3
    elif current >= previous:
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


def _dfs_scores(n: int, distinct_only: bool) -> tuple:
    """_TRANSITIONS를 따라 n구간 점수를 모두 모은 뒤 S(전구간 신고)·C(0)를 뺀 중간 점수 (오름차순).
    distinct_only: 서로 다른 저가만으로 가능한 경로만 (같은 저가 규칙 이전의 등급 눈금)."""
    max_s = _max_seq_score(n)
    scores: set[int] = set()
    stack = [(0, 'flat', 0)]        # (depth, 상태, 점수)
    while stack:
        depth, state, score = stack.pop()
        if depth == n:
            if 0 < score < max_s:
                scores.add(score)
            continue
        for v, nxt, distinct in _TRANSITIONS[state]:
            if distinct or not distinct_only:
                stack.append((depth + 1, nxt, score + v * (4 ** depth)))
    return tuple(sorted(scores))


@lru_cache(maxsize=8)
def _achievable_intermediate_scores(n: int) -> tuple:
    """실제로 달성 가능한 중간 점수 집합 (S·C 제외) — 같은 저가 포함. 여기 없는 점수만 '—'."""
    return _dfs_scores(n, distinct_only=False)


@lru_cache(maxsize=8)
def _grade_scale(n: int) -> tuple:
    """등급 눈금 = 서로 다른 저가로만 된 패턴의 중간 점수. 같은 저가 경로가 늘어도 기존 패턴의 순위·등급이
    밀리지 않게 순위는 이 눈금으로 매긴다(#40 M4)."""
    return _dfs_scores(n, distinct_only=True)


def _grade_from_score(t_score: int, n: int) -> str:
    max_s = _max_seq_score(n)
    if t_score == max_s:
        return 'S'
    if t_score == 0:
        return 'C'
    if t_score not in _achievable_intermediate_scores(n):
        return '—'
    scale = _grade_scale(n)
    if not scale:               # 날짜 2개: 서로 다른 저가로는 S·C뿐 — 같은 저가('고')는 가장 낮은 중간 등급
        return _GRADES_ASC[0]
    # 눈금에 있는 점수는 그 순위, 같은 저가로만 나오는 점수는 바로 아래 눈금 점수의 순위(보수적)
    rank = max(bisect_right(scale, t_score) - 1, 0)
    idx = min(int(rank * 10 / len(scale)), 9)
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
    raw = sorted({pd.Timestamp(str(d).strip()) for d in swing_dates if str(d).strip()})
    if len(raw) < 2 or stock_df.empty:
        return {'grade': '—', 'pattern': '', 'score': 0.0}

    days = [_trading_day(stock_df, d) for d in raw]
    if any(d is None for d in days):
        return {'grade': '—', 'pattern': '데이터부족', 'score': 0.0}
    # 같은 거래일로 매핑되는 날짜(금·토 등)는 하나로 — 같은 저가가 두 번 들어가 패턴이 어긋나지 않게
    days = sorted(set(days))
    if len(days) < 2:
        return {'grade': '—', 'pattern': '', 'score': 0.0}

    prices = [float(stock_df.loc[stock_df.index <= d, 'Low'].iloc[-1]) for d in days]
    n = len(days) - 1
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
