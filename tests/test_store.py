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


def test_load_snapshot_memoized_until_file_changes(tmp_store, monkeypatch):
    """같은 파일은 1회만 파싱 (티커별 반복 로드 O(N²) 방지), 파일 갱신 시 무효화."""
    snap_v1 = {'market': 'US', 'data': {'AAPL': store._df_to_records(_sample_df())}}
    (tmp_store / 'US.json').write_text(json.dumps(snap_v1), encoding='utf-8')

    parses = []
    real_loads = json.loads
    monkeypatch.setattr(store.json, 'loads', lambda s: (parses.append(1), real_loads(s))[1])

    store.load_snapshot('US')
    store.load_snapshot('US')
    store.load_snapshot('US')
    assert len(parses) == 1                      # 메모 히트

    snap_v2 = {'market': 'US', 'data': {}}
    store.save_snapshot('US', snap_v2)
    assert store.load_snapshot('US') == snap_v2  # 저장 직후 최신 반영
    fresh = store.load_snapshot('US')
    assert fresh == snap_v2


def test_refetch_market_full_replace(tmp_store, monkeypatch):
    old = {'market': 'US', 'fetched_at': '2026-07-01T07:00:00+09:00',
           'data': {'OLD': store._df_to_records(_sample_df())}}
    (tmp_store / 'US.json').write_text(json.dumps(old), encoding='utf-8')
    monkeypatch.setattr(store, 'fetch_daily_bulk_us',
                        lambda tickers, days: {t: _sample_df() for t in tickers})
    monkeypatch.setattr(store, 'fetch_daily', lambda t, market, days: _sample_df())
    store.refetch_market('US', ['AAPL', 'TSLA'])
    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert set(saved['data'].keys()) == {'AAPL', 'TSLA'}   # OLD 제거 = 전체 교체
    assert saved['last_trading_date'] == '2026-07-21'
    assert saved['ticker_count'] == 2
    assert saved['fetched_at'] is not None


def test_build_snapshot_records_failures(tmp_store):
    def flaky(t, market, days):
        if t == 'BAD':
            raise RuntimeError('boom')
        return _sample_df()
    snap = store.build_market_snapshot('US', ['AAPL', 'BAD'], fetch_fn=flaky, throttle_sec=0)
    assert snap['ticker_count'] == 1
    assert snap['failed'] == ['BAD']
    assert not (tmp_store / 'US.json').exists()   # build는 저장하지 않는다


def test_build_snapshot_us_uses_bulk(tmp_store, monkeypatch):
    """US는 벌크 수집 경로 — 프로세스당 요청 수 제한(~245회) 회피."""
    monkeypatch.setattr(store, 'fetch_daily_bulk_us',
                        lambda tickers, days: {'AAPL': _sample_df(), 'TSLA': _sample_df()})
    monkeypatch.setattr(store, 'fetch_daily',
                        lambda *a, **k: pytest.fail('벌크 성공 시 개별 호출 금지'))
    snap = store.build_market_snapshot('US', ['AAPL', 'TSLA'], throttle_sec=0)
    assert snap['ticker_count'] == 2
    assert snap['failed'] == []
    assert snap['last_trading_date'] == '2026-07-21'


def test_build_snapshot_us_bulk_miss_retries_individually(tmp_store, monkeypatch):
    """벌크에서 빠진 티커는 개별 폴백 1회 시도 후 실패 기록."""
    monkeypatch.setattr(store, 'fetch_daily_bulk_us',
                        lambda tickers, days: {'AAPL': _sample_df()})
    calls = []
    def single(t, market, days):
        calls.append(t)
        if t == 'GOOD':
            return _sample_df()
        return pd.DataFrame()
    monkeypatch.setattr(store, 'fetch_daily', single)
    snap = store.build_market_snapshot('US', ['AAPL', 'GOOD', 'BAD'], throttle_sec=0)
    assert snap['ticker_count'] == 2
    assert snap['failed'] == ['BAD']
    assert calls == ['GOOD', 'BAD']   # 벌크 성공분은 개별 호출 안 함


def test_build_snapshot_dedupes_tickers(tmp_store):
    """중복 티커는 1회만 수집 — 성공률 분모 왜곡 방지 (us.tickers 390줄 중 유니크 249개 사례)."""
    calls = []
    def fetch(t, market, days):
        calls.append(t)
        return _sample_df()
    snap = store.build_market_snapshot('KR_KOSPI', ['005930', '005930', '000660'],
                                       fetch_fn=fetch, throttle_sec=0)
    assert calls == ['005930', '000660']
    assert snap['ticker_count'] == 2
    assert snap['failed'] == []


def test_build_snapshot_kr_stays_per_ticker(tmp_store, monkeypatch):
    """KR은 pykrx 패치 체인이 필요하므로 개별 수집 경로 유지."""
    monkeypatch.setattr(store, 'fetch_daily_bulk_us',
                        lambda *a, **k: pytest.fail('KR은 벌크 경로 금지'))
    monkeypatch.setattr(store, 'fetch_daily', lambda t, market, days: _sample_df())
    snap = store.build_market_snapshot('KR_KOSPI', ['005930'], throttle_sec=0)
    assert snap['ticker_count'] == 1


def test_build_snapshot_retries_transient_failures(tmp_store):
    """일시 실패(레이트리밋 등)는 재시도 패스에서 회복돼야 한다."""
    calls = {}
    def transient(t, market, days):
        calls[t] = calls.get(t, 0) + 1
        if t == 'FLAKY' and calls[t] == 1:
            return pd.DataFrame()   # 첫 시도만 빈 응답
        return _sample_df()
    snap = store.build_market_snapshot('US', ['AAPL', 'FLAKY'], fetch_fn=transient, throttle_sec=0)
    assert snap['ticker_count'] == 2
    assert snap['failed'] == []
    assert calls['FLAKY'] == 2


# ── get_freshness (2026-07 기준: 20=월 21=화 22=수 23=목 24=금 25=토 26=일) ──

def _write_meta(tmp_store, market, fetched_at):
    snap = {'market': market, 'fetched_at': fetched_at,
            'last_trading_date': '2026-07-22', 'data': {}}
    (tmp_store / f'{market}.json').write_text(json.dumps(snap), encoding='utf-8')


def test_freshness_fresh_after_batch(tmp_store):
    _write_meta(tmp_store, 'KR_KOSPI', '2026-07-22T16:00:00+09:00')  # 수 16:00 수집
    now = datetime(2026, 7, 22, 18, 0, tzinfo=KST)                    # 수 18:00
    assert store.get_freshness('KR_KOSPI', now=now)['is_stale'] is False


def test_freshness_stale_when_batch_missed(tmp_store):
    _write_meta(tmp_store, 'KR_KOSPI', '2026-07-22T16:00:00+09:00')  # 수 16:00 수집
    now = datetime(2026, 7, 23, 23, 0, tzinfo=KST)  # 목 23:00 — 목 16:00 배치+유예 6h 경과
    assert store.get_freshness('KR_KOSPI', now=now)['is_stale'] is True


def test_freshness_weekend_not_stale(tmp_store):
    _write_meta(tmp_store, 'KR_KOSPI', '2026-07-24T16:00:00+09:00')  # 금 16:00 수집
    now = datetime(2026, 7, 26, 14, 0, tzinfo=KST)                    # 일 14:00
    assert store.get_freshness('KR_KOSPI', now=now)['is_stale'] is False


def test_freshness_us_monday_morning_not_stale(tmp_store):
    # US 배치는 KST 화~토 07:00 — 월요일 오전엔 토요일 배치가 최신이 맞다
    _write_meta(tmp_store, 'US', '2026-07-25T07:00:00+09:00')        # 토 07:00 수집
    now = datetime(2026, 7, 27, 10, 0, tzinfo=KST)                    # 월 10:00
    assert store.get_freshness('US', now=now)['is_stale'] is False


def test_freshness_missing_file_not_stale(tmp_store):
    fr = store.get_freshness('US')
    assert fr['is_stale'] is False
    assert fr['fetched_at'] is None
