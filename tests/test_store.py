import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from data import store

KST = timezone(timedelta(hours=9))


def _sample_df():
    idx = pd.DatetimeIndex(pd.to_datetime(['2026-07-20', '2026-07-21']))
    return pd.DataFrame({
        'Open':   [100.0, 102.0],
        'High':   [105.0, 106.0],
        'Low':    [99.0, 101.0],
        'Close':  [104.0, 103.0],
        'Volume': [1000.0, 1100.0],
    }, index=idx)


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'OHLCV_DIR', tmp_path)
    return tmp_path


def test_records_roundtrip():
    df = _sample_df()
    rec = store._df_to_records(df)
    back = store._records_to_df(rec)
    assert list(back.columns) == ['Open', 'High', 'Low', 'Close', 'Volume']
    assert back.index.equals(df.index)
    assert float(back['Close'].iloc[-1]) == 103.0


def test_load_daily_hit_no_network(tmp_store, monkeypatch):
    snap = {'market': 'US', 'data': {'AAPL': store._df_to_records(_sample_df())}}
    (tmp_store / 'US.json').write_text(json.dumps(snap), encoding='utf-8')
    monkeypatch.setattr(store, 'fetch_daily',
                        lambda *a, **k: pytest.fail('스냅샷 히트 시 네트워크 호출 금지'))
    df = store.load_daily('AAPL', 'US')
    assert df.index[-1] == pd.Timestamp('2026-07-21')
    assert float(df['Close'].iloc[-1]) == 103.0


def test_load_daily_miss_fallback_and_merge(tmp_store, monkeypatch):
    monkeypatch.setattr(store, 'fetch_daily', lambda *a, **k: _sample_df())
    df = store.load_daily('TSLA', 'US')
    assert not df.empty
    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert 'TSLA' in saved['data']
    assert saved.get('fetched_at') is None  # 미스 병합은 메타를 갱신하지 않는다


def test_load_index_hit(tmp_store, monkeypatch):
    snap = {'market': 'indices', 'data': {'KOSPI': store._df_to_records(_sample_df())}}
    (tmp_store / 'indices.json').write_text(json.dumps(snap), encoding='utf-8')
    monkeypatch.setattr(store, 'fetch_index_daily',
                        lambda *a, **k: pytest.fail('스냅샷 히트 시 네트워크 호출 금지'))
    df = store.load_index('KOSPI')
    assert float(df['Close'].iloc[-1]) == 103.0
