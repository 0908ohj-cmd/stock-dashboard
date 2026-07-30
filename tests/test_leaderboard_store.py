import json
from datetime import datetime

import pytest

from data import leaderboard_store as store


@pytest.fixture
def lb_dir(tmp_path, monkeypatch):
    """LEADERBOARD_DIR를 임시 디렉토리로 치환."""
    d = tmp_path / 'leaderboard'
    d.mkdir()
    monkeypatch.setattr(store, 'LEADERBOARD_DIR', d)
    return d


def _write(lb_dir, market, payload):
    (lb_dir / f'{market}.json').write_text(
        json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _envelope(items, source_updated_at='2026-07-29T07:02:16'):
    return {
        'market': 'US',
        'synced_at': '2026-07-29T07:05:00',
        'source_updated_at': source_updated_at,
        'count': len(items),
        'items': items,
    }


def test_load_정상_파일(lb_dir):
    _write(lb_dir, 'us', _envelope([{'ticker': 'DELL', 'rank': 1}]))
    result = store.load('us')
    assert result['count'] == 1
    assert result['items'][0]['ticker'] == 'DELL'


def test_load_파일_없으면_빈_봉투(lb_dir):
    result = store.load('us')
    assert result['items'] == []
    assert result['count'] == 0
    assert result['source_updated_at'] is None


def test_load_items_빈_배열도_그대로_반환(lb_dir):
    """리더보드 0개는 정상 상태 — 예외 없이 빈 리스트를 준다."""
    _write(lb_dir, 'kr', _envelope([]))
    result = store.load('kr')
    assert result['items'] == []
    assert result['source_updated_at'] == '2026-07-29T07:02:16'


def test_load_깨진_JSON이면_빈_봉투(lb_dir):
    (lb_dir / 'us.json').write_text('{not json', encoding='utf-8')
    result = store.load('us')
    assert result['items'] == []


def test_get_tickers(lb_dir):
    _write(lb_dir, 'kr', _envelope([{'ticker': '005930'}, {'ticker': '000660'}]))
    assert store.get_tickers('kr') == {'005930', '000660'}


def test_get_tickers_파일_없으면_빈_집합(lb_dir):
    assert store.get_tickers('us') == set()


def test_freshness_정시_갱신_직후는_신선(lb_dir):
    """US 배치 07:00 → 07:02 갱신, 09:00 조회. stale이면 안 된다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-29T07:02:16'))
    now = datetime(2026, 7, 29, 9, 0)          # 수요일
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_유예시간_내에는_판정_보류(lb_dir):
    """갱신이 안 됐어도 예정시각+6h 전이면 아직 stale로 보지 않는다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-28T07:02:16'))
    now = datetime(2026, 7, 29, 10, 0)         # 07:00+6h=13:00 이전
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_유예시간_지나고_미갱신이면_stale(lb_dir):
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-28T07:02:16'))
    now = datetime(2026, 7, 29, 14, 0)         # 07:00+6h=13:00 경과
    assert store.get_freshness('us', now)['is_stale'] is True


def test_freshness_일요일_조회시_금요일_데이터는_오탐_아님(lb_dir):
    """주말엔 배치가 없다 — due를 금요일로 되감지 않으면 일요일에 오탐한다.

    되감기가 없으면 due=일 07:00 이 되어 금요일 갱신분이 stale로 잘못 잡힌다.
    """
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-31T07:02:16'))
    now = datetime(2026, 8, 2, 14, 0)          # 일요일 14:00 (유예 6h 경과 시점)
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_월요일_오전은_유예시간이_보호(lb_dir):
    """월요일 07:00 배치 전/직후에는 금요일 데이터라도 아직 판정하지 않는다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-31T07:02:16'))
    now = datetime(2026, 8, 3, 10, 0)          # 월요일 10:00 < 07:00+6h
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_월요일_오후_배치_미실행이면_stale(lb_dir):
    """유예가 지나도록 월요일 배치가 안 돌았으면 금요일 데이터는 오래된 것이 맞다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-31T07:02:16'))
    now = datetime(2026, 8, 3, 15, 0)          # 월요일 15:00 > 07:00+6h
    assert store.get_freshness('us', now)['is_stale'] is True


def test_freshness_kr은_1630_기준(lb_dir):
    """KR 예정시각 16:30 — 당일 12:00 조회 시 직전 due는 어제 16:30."""
    _write(lb_dir, 'kr', _envelope([], source_updated_at='2026-07-28T16:35:00'))
    now = datetime(2026, 7, 29, 12, 0)
    assert store.get_freshness('kr', now)['is_stale'] is False


def test_freshness_파일_없으면_데이터없음_stale_아님(lb_dir):
    f = store.get_freshness('us', datetime(2026, 7, 29, 14, 0))
    assert f['has_data'] is False
    assert f['is_stale'] is False
