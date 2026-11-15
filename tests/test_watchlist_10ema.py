"""10EMA 탭 행의 타점 — 케이스1 = 10EMA, 그 밖(케이스2·돌파완료·이탈) = 기준봉 고가 (#46 L15).

ui 모듈이라 streamlit·st_aggrid가 없는 최소 환경에선 건너뛴다. CI는 requirements.txt를 설치해 돈다.
시세·종목명·기준봉 탐지는 대역으로 바꿔 상태별 타점 계산만 본다.
"""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip('streamlit')
pytest.importorskip('st_aggrid')
from strategy.pivot_candle import pp_prefilter  # noqa: E402
from ui import watchlist_10ema as w10  # noqa: E402


def _df():
    """ADR 7% 정배열 상승 80일 — pp_prefilter 통과."""
    c = np.linspace(50, 100, 80)
    return pd.DataFrame({'Open': c, 'High': c * 1.035, 'Low': c * 0.965, 'Close': c, 'Volume': 1e6},
                        index=pd.bdate_range('2026-01-01', periods=len(c)))


@pytest.fixture
def stock(monkeypatch):
    df = _df()
    pivot = {'date': df.index[-5], 'vol_ratio': 3.0, 'high': 96.0, 'low': 90.0, 'midline': 93.0,
             'close': 95.0, 'cluster_end': df.index[-5], 'invalidated': False}
    monkeypatch.setattr(w10, 'load_daily', lambda ticker, market=None: df)
    monkeypatch.setattr(w10, 'get_stock_name', lambda ticker, market: ticker)
    monkeypatch.setattr(w10, 'find_pivot_candle', lambda d: pivot)
    return df, pivot


def _row(monkeypatch, state):
    monkeypatch.setattr(w10, 'classify_case', lambda d, p: state)
    row = w10._process_one('TEST', 'US')
    assert row is not None
    return row


def test_케이스1_타점은_10EMA(stock, monkeypatch):
    df, _ = stock
    row = _row(monkeypatch, '셋업(케이스1)')
    assert row['타점'] == round(pp_prefilter(df)['ema10'], 2)
    assert row['현재→타점%'] == round((100.0 / row['타점'] - 1) * 100, 2)


@pytest.mark.parametrize('state', ['셋업(케이스2)', '돌파완료', '10EMA이탈', '중간선이탈'])
def test_케이스1이_아니면_타점은_기준봉_고가(stock, monkeypatch, state):
    row = _row(monkeypatch, state)
    assert row['타점'] == 96.0
    assert row['현재→타점%'] == round((100.0 / 96.0 - 1) * 100, 2)
    assert row['횡보일수'] == 4 and row['기준봉일'] == str(stock[0].index[-5].date())


def test_기준봉이_없으면_타점_0(monkeypatch, stock):
    monkeypatch.setattr(w10, 'find_pivot_candle', lambda d: None)
    row = _row(monkeypatch, '없음')
    assert (row['타점'], row['현재→타점%'], row['이전상승%'], row['기준봉일']) == (0.0, 0.0, 0.0, '')
