import pandas as pd
import pytest
from strategy.rs_correction import calc_correction_rs


def _make_df(closes, opens=None, volumes=None, start='2026-01-01'):
    n       = len(closes)
    dates   = pd.date_range(start, periods=n, freq='B')
    opens   = opens   or closes[:]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        'Open':   opens,
        'High':   [c * 1.01 for c in closes],
        'Low':    [c * 0.99 for c in closes],
        'Close':  closes,
        'Volume': volumes,
    }, index=dates)


def test_excess_pct_positive_when_stock_outperforms():
    dates      = pd.date_range('2026-01-01', periods=20, freq='B')
    idx_df     = _make_df([100 - i for i in range(20)])       # -19%
    stk_df     = _make_df([100 - i * 0.25 for i in range(20)]) # -4.75%
    result     = calc_correction_rs(
        stk_df, idx_df,
        correction_start=dates[0],
        jjin_date=dates[-1],
    )
    assert result['excess_pct'] > 0
    assert result['stock_pct'] > result['index_pct']


def test_lead_days_positive_when_stock_bottoms_first():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    # 지수: 20일 하락 후 10일 반등
    idx_closes = [100 - i * 2 for i in range(20)] + [60 + i * 2 for i in range(10)]
    # 종목: 10일 하락 후 20일 반등  (10일 먼저 저점)
    stk_closes = [100 - i * 4 for i in range(10)] + [60 + i * 2 for i in range(20)]
    result = calc_correction_rs(
        _make_df(stk_closes), _make_df(idx_closes),
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['lead_days'] > 0


def test_lead_days_negative_when_stock_bottoms_later():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    idx_closes = [100 - i * 4 for i in range(10)] + [60 + i * 2 for i in range(20)]
    stk_closes = [100 - i * 2 for i in range(20)] + [60 + i * 2 for i in range(10)]
    result = calc_correction_rs(
        _make_df(stk_closes), _make_df(idx_closes),
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['lead_days'] < 0


def test_lead_days_captures_bottom_before_correction_start():
    # 지수 고점 → EMA21 이탈(correction_start) 이전에 종목이 이미 저점
    # 구조: 40일, 고점=day5, EMA21이탈=day15, 종목저점=day12, 지수저점=day25
    n = 40
    dates = pd.date_range('2026-01-01', periods=n, freq='B')

    # 지수: day0~5 상승, day5~25 하락, day25~ 반등
    idx_closes = (
        [100 + i * 2 for i in range(6)]          # 0~5: 100→110 (고점)
        + [110 - i * 2 for i in range(20)]        # 6~25: 110→70 (저점)
        + [70 + i * 2 for i in range(14)]         # 26~39: 반등
    )
    # 종목: day12에 저점, 이후 바로 반등 (correction_start=day15 이전)
    stk_closes = (
        [100 + i * 1 for i in range(13)]          # 0~12: 완만 상승
        + [112 - i * 3 for i in range(1)]         # 12: 저점 109
        + [109 + i * 1 for i in range(26)]        # 13~38: 서서히 반등
    )

    idx_df = _make_df(idx_closes)
    stk_df = _make_df(stk_closes)
    correction_start = dates[15]  # EMA21 이탈일

    result = calc_correction_rs(stk_df, idx_df, correction_start=correction_start, jjin_date=dates[-1])

    # 종목(day12) < correction_start(day15) < 지수저점(day25) → lead_days 양수여야 함
    assert result['lead_days'] > 0


def test_returns_zeros_on_insufficient_data():
    dates  = pd.date_range('2026-01-01', periods=1, freq='B')
    idx_df = _make_df([100])
    stk_df = _make_df([50])
    result = calc_correction_rs(
        stk_df, idx_df,
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['excess_pct'] == 0.0
    assert result['lead_days']  == 0


def test_excess_adr_normalizes_by_volatility():
    # 조정 전 ADR 설정: stock A (변동성 큰) pre-correction 고변동, B (변동성 작은) 저변동
    # 조정 구간: 둘 다 excess_pct 동일 → excess_adr는 변동성 작은 쪽이 더 높아야 함
    pre = list(range(100, 86, -1))       # 14일 pre-correction 데이터
    corr = [85, 84, 83, 82, 83, 84, 85] # 7일 조정 구간

    # high_adr: High/Low 범위를 2%로 설정 (실제 calc_adr 공식: (H-L)/C * 100)
    # _make_df 기본: High = close*1.01, Low = close*0.99 → ADR ≈ 2%
    high_adr_df = pd.DataFrame({
        'Open':   pre + corr,
        'High':   [c * 1.05 for c in pre] + [c * 1.05 for c in corr],  # ADR ≈ 10%
        'Low':    [c * 0.95 for c in pre] + [c * 0.95 for c in corr],
        'Close':  pre + corr,
        'Volume': [1_000_000] * (len(pre) + len(corr)),
    }, index=pd.date_range('2026-01-01', periods=len(pre) + len(corr), freq='B'))

    low_adr_df = pd.DataFrame({
        'Open':   pre + corr,
        'High':   [c * 1.01 for c in pre] + [c * 1.01 for c in corr],  # ADR ≈ 2%
        'Low':    [c * 0.99 for c in pre] + [c * 0.99 for c in corr],
        'Close':  pre + corr,
        'Volume': [1_000_000] * (len(pre) + len(corr)),
    }, index=pd.date_range('2026-01-01', periods=len(pre) + len(corr), freq='B'))

    idx_df = _make_df([100 - i for i in range(len(pre) + len(corr))])
    correction_start = high_adr_df.index[len(pre)]

    r_high = calc_correction_rs(high_adr_df, idx_df, correction_start, None)
    r_low  = calc_correction_rs(low_adr_df,  idx_df, correction_start, None)

    # 같은 종목 데이터이므로 excess_pct는 동일
    assert r_high['excess_pct'] == r_low['excess_pct']
    # 변동성 작은 쪽이 RS/ADR 더 높아야 함
    assert r_low['excess_adr'] > r_high['excess_adr']
