import pandas as pd
import pytest
from strategy.watchlist_rows import build_rows


def _mk(closes, dates, spread=0.02):
    return pd.DataFrame({
        'Open':   closes,
        'High':   [c * (1 + spread) for c in closes],
        'Low':    [c * (1 - spread) for c in closes],
        'Close':  closes,
        'Volume': [1_000_000] * len(closes),
    }, index=pd.DatetimeIndex(dates))


@pytest.fixture
def dates60():
    return pd.bdate_range('2026-01-02', periods=60)


@pytest.fixture
def index_df(dates60):
    # 40일 횡보 후 20일 하락
    closes = [100.0] * 40 + [99.0 - i for i in range(20)]
    return _mk(closes, dates60)


def _stock_frames(dates60):
    return {
        'AAA': _mk([100.0 + i for i in range(60)], dates60),               # ADR ≈ 4%
        'BBB': _mk([100.0] * 60, dates60, spread=0.005),                   # ADR ≈ 1% → KR 최소 2% 미달
    }


def _call(tickers, index_df, dates60, **kwargs):
    frames = _stock_frames(dates60)
    return build_rows(
        tickers, 'KR_KOSPI', index_df,
        correction_start=dates60[40], jjin_date=None,
        fetch=lambda ticker, market, days: frames[ticker],
        get_name=lambda t, m: f'{t}명',
        sectors_fn=lambda ts, m: {t: '테스트섹터' for t in ts},
        **kwargs,
    )


def test_asof_freezes_computation_to_that_date(index_df, dates60):
    """asof를 주면 그 날짜까지의 데이터만으로 계산 → DAY3 추가 후보가 진짜로 동결된다."""
    asof_res = _call(['AAA'], index_df, dates60, asof=dates60[49])
    live_res = _call(['AAA'], index_df, dates60)

    asof_row = asof_res['rows'][0]
    live_row = live_res['rows'][0]

    assert asof_row['Close'] == 149.0   # dates60[49]의 종가
    assert live_row['Close'] == 159.0   # 마지막 날 종가
    assert asof_row['조정RS%'] != live_row['조정RS%']


def test_adr_skipped_count_returned(index_df, dates60):
    res = _call(['AAA', 'BBB'], index_df, dates60)
    assert [r['Ticker'] for r in res['rows']] == ['AAA']
    assert res['adr_skipped'] == 1


def test_row_has_expected_display_fields(index_df, dates60):
    row = _call(['AAA'], index_df, dates60)['rows'][0]
    assert row['종목명'] == 'AAA명'
    assert row['섹터'] == '테스트섹터'
    for key in ('ADR', 'Close', '등락%', '고점대비%', '저점선행',
                '조정RS%', 'RS/ADR', '이평선위치', 'ma_above_count',
                '거래량비%', '양봉비%'):
        assert key in row
