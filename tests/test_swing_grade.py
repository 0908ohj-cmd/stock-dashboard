"""스윙 등급 (strategy/swing_grade.py).

#40 M4 — 같은 저가 처리:
- 직전 저점과 같은 저가 = 고(2) (쌍바닥 = 지지 확인). 예전엔 저(1)로 레이블돼 전이표에 없는 경로가 되어 '—'(미분류)가 났다
- 같은 거래일로 매핑되는 날짜(금·토 등)는 하나로 합친다
- 서로 다른 저가로만 된 기존 패턴의 등급은 그대로다
"""
import itertools

import pandas as pd
import pytest

from strategy import swing_grade as sg
from strategy.swing_grade import calc_swing_grade

_GRADES_ASC = ['B--', 'B-', 'B', 'B+', 'B++', 'A--', 'A-', 'A', 'A+', 'A++']


def _df(lows, start='2026-03-02'):
    idx = pd.bdate_range(start, periods=len(lows))
    return pd.DataFrame({'Open': lows, 'High': lows, 'Low': lows, 'Close': lows, 'Volume': 1}, index=idx)


def _grade(lows):
    df = _df(lows)
    return calc_swing_grade(df, [str(d.date()) for d in df.index])


# ── 레이블 정의(명세) — 테스트 쪽 기준 ─────────────────────
def _spec_labels(prices):
    """신고(3): 구간 최고 갱신 / 고(2): 직전 이상(같으면 고) / 저(1): 직전 미만·최저 유지 / 신저(0): 최저 갱신."""
    out, lo, hi = [], prices[0], prices[0]
    for prev, cur in zip(prices, prices[1:]):
        out.append(3 if cur > hi else 2 if cur >= prev else 1 if cur >= lo else 0)
        lo, hi = min(lo, cur), max(hi, cur)
    return out


def _score(labels):
    return sum(v * 4 ** i for i, v in enumerate(labels))


def _intermediates(seqs, n):
    return sorted({_score(_spec_labels(p)) for p in seqs} - {0, 4 ** n - 1})


# ── 같은 저가 = 고 ─────────────────────────────────────────
def test_equal_low_on_first_segment_is_high():
    r = _grade([95, 95, 97])
    assert r['pattern'] == '고→신고'
    assert r['grade'] != '—'


def test_double_bottom_after_new_low_grades_like_slightly_higher_low():
    """95 → 93 → 93 → 96(쌍바닥)은 93.1로 살짝 높인 대조군과 같은 패턴·등급."""
    r, ctrl = _grade([95, 93, 93, 96]), _grade([95, 93, 93.1, 96])
    assert r['pattern'] == ctrl['pattern'] == '신저→고→신고'
    assert r['grade'] == ctrl['grade'] == 'A+'


def test_equal_low_right_after_new_high_is_high():
    r = _grade([95, 97, 97])
    assert r['pattern'] == '신고→고'
    assert r['grade'] != '—'


def test_two_dates_with_equal_low_get_middle_grade():
    r = _grade([95, 95])
    assert r['pattern'] == '고'
    assert r['grade'] not in ('—', 'S', 'C')


@pytest.mark.parametrize('n', [1, 2, 3])
def test_no_unclassified_grade_for_any_low_sequence(n):
    """같은 값이 섞인 모든 저가 순서(약순서 전부)에서 '—'가 나오지 않는다."""
    for prices in itertools.product(range(n + 1), repeat=n + 1):
        r = _grade([90.0 + p for p in prices])
        assert r['grade'] != '—', (prices, r)


@pytest.mark.parametrize('n', [1, 2, 3, 4, 5])
def test_achievable_scores_match_label_definition(n):
    """DFS 달성 가능 점수 = 같은 값 포함 모든 저가 순서를 명세대로 레이블한 점수 (넘치지도 모자라지도 않게)."""
    brute = _intermediates(itertools.product(range(n + 1), repeat=n + 1), n)
    assert list(sg._achievable_intermediate_scores(n)) == brute


# ── 기존 패턴 등급 유지 ───────────────────────────────────
@pytest.mark.parametrize('lows, grade, pattern', [
    ([95, 93, 93.1, 96], 'A+', '신저→고→신고'),
    ([95, 93.1, 96], 'A', '신저→신고'),
    ([95, 97, 96], 'B', '신고→저'),
    ([90, 95, 98, 99], 'S', '신고→신고→신고'),
    ([99, 95, 92, 90], 'C', '신저→신저→신저'),
    ([95, 93, 94, 92, 96], 'A', '신저→고→신저→신고'),
    ([95, 97, 96, 98], 'A+', '신고→저→신고'),
])
def test_existing_patterns_keep_grade(lows, grade, pattern):
    """수정 전 코드가 낸 등급(서로 다른 저가) 그대로."""
    r = _grade(lows)
    assert (r['grade'], r['pattern']) == (grade, pattern)


@pytest.mark.parametrize('n', [2, 3, 4, 5])
def test_all_distinct_low_patterns_keep_previous_scale(n):
    """서로 다른 저가로 된 모든 패턴의 등급 = 그 패턴들끼리만 순위를 매긴 예전 눈금의 등급.
    (같은 값 경로가 늘어나도 기존 패턴 등급이 밀리지 않는다)"""
    perms = list(itertools.permutations(range(n + 1)))
    scale = _intermediates(perms, n)
    for p in perms:
        t = _score(_spec_labels(p))
        want = 'S' if t == 4 ** n - 1 else 'C' if t == 0 else _GRADES_ASC[min(int(scale.index(t) * 10 / len(scale)), 9)]
        assert sg._grade_from_score(t, n) == want, (p, t)


# ── 날짜 정규화 ───────────────────────────────────────────
def test_dates_mapping_to_same_trading_day_are_merged():
    """토요일은 금요일 저가로 매핑된다 — 같은 거래일이 두 번 들어가 '저'·'—'가 나오면 안 된다."""
    df = _df([95, 93, 96, 94, 97])            # 2026-03-02(월) ~ 03-06(금)
    base = calc_swing_grade(df, ['2026-03-02', '2026-03-03', '2026-03-06'])
    with_sat = calc_swing_grade(df, ['2026-03-02', '2026-03-03', '2026-03-06', '2026-03-07'])
    other_fmt = calc_swing_grade(df, ['2026-03-02', '2026-03-03', '2026-03-06', '2026-03-06 00:00:00'])
    assert with_sat == base
    assert other_fmt == base
    assert base['pattern'] == '신저→신고'


def test_all_dates_on_one_trading_day_is_not_graded():
    df = _df([95, 93, 96, 94, 97])
    r = calc_swing_grade(df, ['2026-03-06', '2026-03-07', '2026-03-08'])     # 금·토·일 → 금 하루
    assert r['grade'] == '—'
