import pandas as pd
import pytest
from strategy.rs_correction import calc_correction_rs, _vol_ratio


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


def test_vol_ratio_max_when_no_down_days():
    """조정 구간에 하락일이 하나도 없는 최강 종목 → 0이 아니라 상한값이어야 한다."""
    df = _make_df([100 + i for i in range(10)], opens=[99 + i for i in range(10)])
    assert _vol_ratio(df) == 9.99


def test_vol_ratio_zero_when_no_up_days():
    df = _make_df([100 - i for i in range(10)], opens=[101 - i for i in range(10)])
    assert _vol_ratio(df) == 0.0


def test_vol_ratio_zero_when_down_days_have_zero_volume():
    """하락일이 존재하는데 거래량이 전부 0인 데이터 결함 → 최강(상한)이 아니라 0."""
    closes  = [101, 99, 102, 98, 103, 97]     # 상승/하락 교차
    opens   = [100, 100, 100, 100, 100, 100]
    volumes = [1_000_000, 0, 1_000_000, 0, 1_000_000, 0]
    df = _make_df(closes, opens=opens, volumes=volumes)
    assert _vol_ratio(df) == 0.0


def test_vol_ratio_capped_at_sentinel():
    """실측비가 상한을 넘으면 캡 — 하락일 0개(상한값) 종목이 뒤로 밀리지 않게 정렬 일관성 유지."""
    closes  = [101, 99, 102, 98, 103, 97]
    opens   = [100, 100, 100, 100, 100, 100]
    volumes = [2_000_000, 100_000, 2_000_000, 100_000, 2_000_000, 100_000]  # 실측비 20
    df = _make_df(closes, opens=opens, volumes=volumes)
    assert _vol_ratio(df) == 9.99


def test_lead_days_uses_actual_trading_days_not_calendar_busdays():
    """연휴로 휴장이 껴 있으면 달력 영업일이 아니라 실제 거래일 수로 선행일을 세야 한다."""
    # 거래일: 1/5~1/16 (10일) + 휴장 1주 + 1/26~1/30 (5일)
    dates = (list(pd.bdate_range('2026-01-05', '2026-01-16'))
             + list(pd.bdate_range('2026-01-26', '2026-01-30')))

    def df_from(closes):
        return pd.DataFrame({
            'Open':   closes,
            'High':   [c * 1.01 for c in closes],
            'Low':    [c * 0.99 for c in closes],
            'Close':  closes,
            'Volume': [1_000_000] * len(closes),
        }, index=pd.DatetimeIndex(dates))

    # 지수 저점: 13번째 행(1/28), 종목 저점: 9번째 행(1/15) → 거래일 기준 선행 4일
    idx_closes = [100 - i * 2 for i in range(13)] + [78.0, 80.0]
    stk_closes = [100 - i * 3 for i in range(9)] + [77 + i for i in range(6)]

    result = calc_correction_rs(df_from(stk_closes), df_from(idx_closes),
                                correction_start=dates[0], jjin_date=dates[-1])
    # busday_count(1/15, 1/28)로 세면 9가 나와버림
    assert result['lead_days'] == 4


def test_lead_days_when_stock_bottom_date_missing_from_index_calendar():
    """종목 저점일이 지수 달력에 없으면(지수 결측일) 직전 지수 거래일로 매핑해야 한다."""
    idx_dates = (list(pd.bdate_range('2026-01-05', '2026-01-14'))          # 1/5~1/14 (8일)
                 + list(pd.bdate_range('2026-01-16', '2026-01-21')))       # 1/15 결측
    stk_dates = list(pd.bdate_range('2026-01-05', '2026-01-21'))           # 1/15 포함 (13일)

    def df_from(closes, dates):
        return pd.DataFrame({
            'Open':   closes,
            'High':   [c * 1.01 for c in closes],
            'Low':    [c * 0.99 for c in closes],
            'Close':  closes,
            'Volume': [1_000_000] * len(closes),
        }, index=pd.DatetimeIndex(dates))

    # 지수 저점: 1/20 (pos 10). 종목 저점: 1/15 → 직전 지수 거래일 1/14(pos 7)로 매핑 → 선행 3일
    idx_closes = [100 - i * 2 for i in range(11)] + [82.0]
    stk_closes = [100 - i * 3 for i in range(9)] + [77 + i for i in range(4)]

    result = calc_correction_rs(df_from(stk_closes, stk_dates), df_from(idx_closes, idx_dates),
                                correction_start=idx_dates[0], jjin_date=idx_dates[-1])
    assert result['lead_days'] == 3
