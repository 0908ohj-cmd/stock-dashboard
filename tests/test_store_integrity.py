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
    # 배치 유니버스(snapshot_tickers)가 실제 data/saved 파일에 좌우되지 않게 — 기본은 비어 있음
    monkeypatch.setattr(store, 'SAVED_DIR', tmp_path / 'saved')
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


# ── #31 부분 재수집 ─────────────────────────────────────────

_NEW_DF = _df(dates=('2026-11-12', '2026-11-13'), closes=(103.0, 110.0))


def _mock_us_fetch(monkeypatch, bulk=None):
    monkeypatch.setattr(store, 'fetch_daily_bulk_us',
                        bulk or (lambda tickers, days: {t: _NEW_DF for t in tickers}))
    monkeypatch.setattr(store, 'fetch_daily',
                        lambda *a, **k: pytest.fail('벌크 성공 시 개별 호출 금지'))


def test_재수집은_대상_밖_종목을_보존한다(tmp_store, monkeypatch):
    """#31 재현: 나스닥 10EMA 재스캔(E1~E3)이 추세추종·주도섹터 종목과 QQQ·IWM을 지웠다."""
    everything = ['AAA', 'BBB', 'E1', 'E2', 'E3', 'IWM', 'QQQ', 'S1', 'S2']
    _write_snapshot(tmp_store, 'US', everything)
    _mock_us_fetch(monkeypatch)

    store.refetch_market('US', ['E1', 'E2', 'E3'], throttle_sec=0)

    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert sorted(saved['data']) == everything
    assert saved['data']['E1']['dates'][-1] == '2026-11-13'   # 대상은 새 수집분
    assert saved['data']['QQQ'] == _rec()                     # 대상 밖은 그대로


def test_부분_재수집은_시장_신선도_메타를_바꾸지_않는다(tmp_store, monkeypatch):
    """일부만 새로 받고 fetched_at을 올리면 ① get_freshness가 남은 종목까지 신선하다고 보고
    ② repo_sync._local_wins가 이 파일을 더 새 원격 배치보다 우선해 지킨다.
    시장 메타는 마지막 '전 종목' 수집 기준으로 두고, 부분 갱신 시각은 따로 남긴다."""
    _write_snapshot(tmp_store, 'US', ['AAA', 'E1'])
    _mock_us_fetch(monkeypatch)

    store.refetch_market('US', ['E1'], throttle_sec=0)

    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert saved['fetched_at'] == BASE_FETCHED_AT
    assert saved['last_trading_date'] == '2026-11-12'
    assert saved['ticker_count'] == 2
    partial_at = datetime.fromisoformat(saved['partial_refetched_at'])
    assert partial_at > datetime.fromisoformat(BASE_FETCHED_AT)


def test_기존_종목을_모두_다시_받으면_fetched_at을_갱신한다(tmp_store, monkeypatch):
    """대상이 기존 종목을 전부 덮으면 배치와 같은 '전 종목' 수집 — 메타를 새로 쓴다."""
    _write_snapshot(tmp_store, 'US', ['AAA', 'E1'],
                    partial_refetched_at='2026-11-12T20:00:00+09:00')
    _mock_us_fetch(monkeypatch)

    store.refetch_market('US', ['AAA', 'E1', 'NEW'], throttle_sec=0)

    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert sorted(saved['data']) == ['AAA', 'E1', 'NEW']
    assert saved['fetched_at'] != BASE_FETCHED_AT
    assert saved['last_trading_date'] == '2026-11-13'
    assert saved['ticker_count'] == 3
    assert 'partial_refetched_at' not in saved


def test_재수집_도중_병합된_미스_종목도_보존한다(tmp_store, monkeypatch):
    """수집에 수 분이 걸리는 동안 다른 탭의 미스 병합이 끼어들 수 있다 — 수집 시작 때
    읽은 스냅샷이 아니라 저장 직전 최신본에 합쳐야 그 종목이 살아남는다."""
    _write_snapshot(tmp_store, 'US', ['AAA', 'E1'])

    def bulk(tickers, days):
        store._merge_ticker('US', 'LATE', _df())   # 수집 도중 끼어든 미스 병합
        return {t: _NEW_DF for t in tickers}

    _mock_us_fetch(monkeypatch, bulk)

    store.refetch_market('US', ['E1'], throttle_sec=0)

    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert sorted(saved['data']) == ['AAA', 'E1', 'LATE']


def test_재수집은_파싱_못_하는_스냅샷을_덮어쓰지_않는다(tmp_store, monkeypatch):
    path = tmp_store / 'US.json'
    broken = '{"market": "US", "fetched_at": "2026-11-12T17:34:00+09:00", "data": {"T0'
    path.write_text(broken, encoding='utf-8')
    _mock_us_fetch(monkeypatch)

    store.refetch_market('US', ['E1'], throttle_sec=0)

    assert path.read_text(encoding='utf-8') == broken


# ── 🔄 데이터 재수집 = 배치와 같은 종목 합집합 (전 종목 수집) ───────────

@pytest.fixture
def saved_dir(tmp_path, monkeypatch):
    d = tmp_path / 'saved'
    d.mkdir()
    monkeypatch.setattr(store, 'SAVED_DIR', d)
    return d


def _tickers_file(saved, name: str, lines: list) -> None:
    (saved / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_snapshot_tickers는_배치_티커_파일의_합집합이다(tmp_store, saved_dir):
    """배치(scripts/fetch_snapshot.load_tickers)가 쓰던 규칙 그대로 — 파일 순서대로 이어 붙이고
    공백 줄·앞뒤 공백·중복은 버리고, 없는 파일은 건너뛴다."""
    _tickers_file(saved_dir, 'us.tickers', ['AAPL', 'NVDA', '', ' TSLA '])
    _tickers_file(saved_dir, 'us_10ema.tickers', ['NVDA', 'PLTR'])
    _tickers_file(saved_dir, 'us_sector.tickers', ['QQQ', 'IWM', 'AAPL'])
    _tickers_file(saved_dir, 'kospi.tickers', ['005930'])          # kospi_10ema.tickers 없음

    assert store.snapshot_tickers('US') == ['AAPL', 'NVDA', 'TSLA', 'PLTR', 'QQQ', 'IWM']
    assert store.snapshot_tickers('KR_KOSPI') == ['005930']
    assert store.snapshot_tickers('KR_KOSDAQ') == []


class _FrozenDatetime(datetime):
    FROZEN = datetime.fromisoformat('2026-11-14T09:00:00+09:00')   # 토요일 — 금 08:00 US 회차 이후

    @classmethod
    def now(cls, tz=None):
        return cls.FROZEN.astimezone(tz) if tz else cls.FROZEN.replace(tzinfo=None)


def test_데이터_재수집은_전_종목_수집이라_fetched_at을_갱신하고_경고가_풀린다(tmp_store, saved_dir, monkeypatch):
    """사이드바 🔄 데이터 재수집은 배치와 같은 합집합을 넘긴다 → 전 종목 수집으로 판정돼
    fetched_at·last_trading_date가 갱신되고, '재수집을 눌러 주세요' 경고(stale·지수 지연)가 풀린다.

    업로드로 목록에서 빠진 옛 종목(GONE)이 스냅샷에 남아 있어도 전 종목 판정을 막지 않는다 —
    배치 유니버스 밖 종목은 원래 fetched_at과 무관하다(미스 병합처럼). 지우지도 않는다."""
    monkeypatch.setattr(store, 'datetime', _FrozenDatetime)
    _tickers_file(saved_dir, 'us.tickers', ['AAA', 'E1'])
    _tickers_file(saved_dir, 'us_sector.tickers', ['QQQ'])
    _write_snapshot(tmp_store, 'US', ['AAA', 'E1', 'QQQ', 'GONE'],
                    fetched_at='2026-11-12T08:14:00+09:00')            # 금요일 회차를 놓친 상태
    (tmp_store / 'indices.json').write_text(json.dumps({
        'market': 'indices', 'fetched_at': '2026-11-14T08:50:00+09:00',
        'data': {'NASDAQ': {'dates': ['2026-11-12', '2026-11-13']}}}), encoding='utf-8')
    _mock_us_fetch(monkeypatch)
    now = _FrozenDatetime.FROZEN
    before = store.get_freshness('US', now)
    assert before['is_stale'] is True and before['behind_index'] == '2026-11-13'

    store.refetch_market('US', store.snapshot_tickers('US'), throttle_sec=0)

    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert saved['fetched_at'] == '2026-11-14T09:00:00+09:00'
    assert saved['last_trading_date'] == '2026-11-13'
    assert 'partial_refetched_at' not in saved
    assert sorted(saved['data']) == ['AAA', 'E1', 'GONE', 'QQQ']
    after = store.get_freshness('US', now)
    assert after['is_stale'] is False and 'behind_index' not in after


def test_배치_유니버스_일부만_받으면_여전히_부분_갱신이다(tmp_store, saved_dir, monkeypatch):
    """탭별 🔄 재스캔처럼 유니버스 일부만 넘기면 메타는 그대로다 (#31 결정 유지)."""
    _tickers_file(saved_dir, 'us.tickers', ['AAA'])
    _tickers_file(saved_dir, 'us_10ema.tickers', ['E1'])
    _write_snapshot(tmp_store, 'US', ['AAA', 'E1'])
    _mock_us_fetch(monkeypatch)

    store.refetch_market('US', ['E1'], throttle_sec=0)

    saved = json.loads((tmp_store / 'US.json').read_text(encoding='utf-8'))
    assert saved['fetched_at'] == BASE_FETCHED_AT
    assert 'partial_refetched_at' in saved
