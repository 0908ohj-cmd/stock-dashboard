import pandas as pd

from data import fetcher


def _flat_df():
    return pd.DataFrame({
        'Open':   [1.0],
        'High':   [2.0],
        'Low':    [0.5],
        'Close':  [1.5],
        'Volume': [100.0],
    }, index=pd.DatetimeIndex(pd.to_datetime(['2026-07-21'])))


def test_bulk_flat_columns_multi_ticker_chunk_skipped(monkeypatch):
    """멀티 티커 청크에서 flat 컬럼이 오면 티커 매칭이 불가 — 오염 방지 위해 청크 스킵."""
    monkeypatch.setattr(fetcher.yf, 'download', lambda *a, **k: _flat_df())
    out = fetcher.fetch_daily_bulk_us(['AAA', 'BBB'], chunk_size=50)
    assert out == {}   # 같은 DataFrame이 두 티커에 배정되면 안 된다


def test_bulk_flat_columns_single_ticker_chunk_ok(monkeypatch):
    """티커 1개짜리 청크의 flat 컬럼은 정상 케이스 — 그대로 매칭."""
    monkeypatch.setattr(fetcher.yf, 'download', lambda *a, **k: _flat_df())
    out = fetcher.fetch_daily_bulk_us(['AAA'], chunk_size=50)
    assert list(out.keys()) == ['AAA']
    assert float(out['AAA']['Close'].iloc[-1]) == 1.5
