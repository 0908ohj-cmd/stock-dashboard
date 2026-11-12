"""스냅샷 무결성 — 병렬 미스 병합(#26)과 부분 재수집(#31)."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pandas as pd
import pytest

from data import store
from tests._write_probe import read_json_or_partial, split_writes

BASE_FETCHED_AT = '2026-11-12T17:34:00+09:00'


def _df(dates=('2026-11-11', '2026-11-12'), closes=(104.0, 103.0)) -> pd.DataFrame:
    return pd.DataFrame({
        'Open':   list(closes),
        'High':   [c + 2 for c in closes],
        'Low':    [c - 2 for c in closes],
        'Close':  list(closes),
        'Volume': [1000.0] * len(closes),
    }, index=pd.DatetimeIndex(pd.to_datetime(list(dates))))


def _rec() -> dict:
    return store._df_to_records(_df())


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'OHLCV_DIR', tmp_path)
    monkeypatch.setattr(store, '_snap_cache', {})
    return tmp_path


def _write_snapshot(dirpath, market: str, tickers: list, **meta) -> dict:
    """배치가 만든 것과 같은 모양의 스냅샷을 디스크에 둔다."""
    snap = {'market': market, 'fetched_at': BASE_FETCHED_AT, 'last_trading_date': '2026-11-12',
            'ticker_count': len(tickers), 'failed': [],
            'data': {t: _rec() for t in tickers}, **meta}
    (dirpath / f'{market}.json').write_text(json.dumps(snap), encoding='utf-8')
    return snap


# ── #26 병렬 미스 병합 ──────────────────────────────────────

def test_병렬_미스_병합이_기존_스냅샷을_지우지_않는다(tmp_store, monkeypatch):
    """#26 재현: 300종목 스냅샷에 없는 60종목을 10스레드로 load_daily.

    쓰기가 원자적이지 않으면 반쯤 쓰인 파일을 다른 스레드가 읽어 파싱에 실패하고, {}에
    종목 1개만 넣어 저장한다 → 기존 300종목과 fetched_at이 사라졌다 (리뷰 재현 4회 중 3회).
    """
    base = _write_snapshot(tmp_store, 'US', [f'T{i:03d}' for i in range(300)])
    monkeypatch.setattr(store, 'fetch_daily', lambda *a, **k: _df())
    hits = split_writes(monkeypatch, tmp_store, on_half=lambda p: time.sleep(0.003))

    misses = [f'M{i:02d}' for i in range(60)]
    errors = []

    def run(t):
        try:
            store.load_daily(t, 'US')
        except Exception as e:   # 공유 dict 직렬화 중 수정(RuntimeError) 등도 잡아 아래에서 단언
            errors.append(repr(e))

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(run, misses))

    saved = read_json_or_partial(tmp_store / 'US.json')
    assert hits, '쪼갠 쓰기 경로를 타지 않았다 — 테스트 전제가 깨졌다'
    assert isinstance(saved, dict), '스냅샷 JSON이 깨진 채 남았다'
    assert len(set(base['data']) - set(saved['data'])) == 0      # 기존 300종목 전부 생존
    assert saved.get('fetched_at') == BASE_FETCHED_AT
    assert set(misses) <= set(saved['data'])                      # 미스 60종목도 모두 병합
    assert errors == []


def test_스냅샷_파싱_실패면_미스_병합이_덮어쓰지_않는다(tmp_store, monkeypatch):
    """파싱 실패를 '빈 스냅샷'으로 보고 종목 1개만 넣어 저장하면 원본이 영영 사라진다."""
    path = tmp_store / 'US.json'
    broken = '{"market": "US", "fetched_at": "2026-11-12T17:34:00+09:00", "data": {"T0'
    path.write_text(broken, encoding='utf-8')
    monkeypatch.setattr(store, 'fetch_daily', lambda *a, **k: _df())

    df = store.load_daily('NEW', 'US')

    assert not df.empty                                   # 호출부는 수집분을 그대로 받는다
    assert path.read_text(encoding='utf-8') == broken     # 파일은 건드리지 않는다


def test_미스_병합은_캐시된_스냅샷_dict를_직접_고치지_않는다(tmp_store, monkeypatch):
    """load_snapshot이 돌려준 dict는 다른 스레드가 읽거나 직렬화하는 중일 수 있다."""
    _write_snapshot(tmp_store, 'US', ['AAA'])
    before = store.load_snapshot('US')
    monkeypatch.setattr(store, 'fetch_daily', lambda *a, **k: _df())

    store.load_daily('NEW', 'US')

    assert set(before['data']) == {'AAA'}
    assert set(store.load_snapshot('US')['data']) == {'AAA', 'NEW'}


def test_save_snapshot은_쓰는_도중에도_이전_파일을_온전히_둔다(tmp_store, monkeypatch):
    old = _write_snapshot(tmp_store, 'US', ['AAA'])
    seen = []
    hits = split_writes(monkeypatch, tmp_store,
                        on_half=lambda p: seen.append(read_json_or_partial(tmp_store / 'US.json')))
    new = {**old, 'data': {'BBB': _rec()}}

    store.save_snapshot('US', new)

    assert hits
    assert seen == [old]                                  # 쓰는 도중 읽어도 이전 버전 그대로
    assert json.loads((tmp_store / 'US.json').read_text(encoding='utf-8')) == new
    assert [p.name for p in tmp_store.iterdir()] == ['US.json']   # 임시 파일이 남지 않는다
