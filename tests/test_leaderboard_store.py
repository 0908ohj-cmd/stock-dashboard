import json
from datetime import datetime, timezone

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
    """US 배치 07:00 → 07:02 갱신, 09:00 조회. 놓친 슬롯 0개."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-29T07:02:16'))
    now = datetime(2026, 7, 29, 9, 0)          # 수요일
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_당일_갱신분은_유예_지나도_신선(lb_dir):
    """오늘 배치가 정상적으로 돌았으면 저녁에 봐도 놓친 슬롯이 없다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-29T07:02:16'))
    now = datetime(2026, 7, 29, 22, 0)         # 유예(13:00) 한참 경과
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_한슬롯_놓쳐도_유예_안이면_판정_보류(lb_dir):
    """오늘(수) 배치가 아직 안 돌았어도 유예 안이면 경고하지 않는다 — 늦게 돌 수 있다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-28T07:02:16'))
    now = datetime(2026, 7, 29, 10, 0)         # 07:00+6h=13:00 이전
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_한슬롯_놓치고_유예_지나면_stale(lb_dir):
    """어제 갱신분 그대로 오늘 유예가 지나면 하루를 더 기다리지 않고 경고한다.

    (구 규칙은 '전전 슬롯' 기준이라 여기서 30시간을 더 침묵했다.)
    """
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-28T07:02:16'))
    now = datetime(2026, 7, 29, 14, 0)         # 수요일, 07:00+6h=13:00 경과
    assert store.get_freshness('us', now)['is_stale'] is True


def test_freshness_두슬롯_이상_놓치면_유예_안이라도_stale(lb_dir):
    """며칠째 멈춘 파이프라인은 아침 09:00(구 규칙의 사각지대)에도 경고해야 한다.

    구 규칙은 '지금 > 직전 예정시각 + 6h'를 AND로 걸어, 평일마다 07:00~13:00
    여섯 시간 동안 몇 주 묵은 데이터를 신선하다고 보고했다.
    """
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-20T07:02:16'))
    now = datetime(2026, 7, 27, 9, 0)          # 월요일 아침, 유예 이전
    assert store.get_freshness('us', now)['is_stale'] is True


def test_freshness_전전_슬롯보다_오래되면_stale(lb_dir):
    """월요일 갱신분을 수요일 오후에 보면 화·수 두 슬롯을 놓쳤다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-27T07:02:16'))
    now = datetime(2026, 7, 29, 14, 0)         # 07:00+6h=13:00 경과
    assert store.get_freshness('us', now)['is_stale'] is True


def test_freshness_예정시각보다_일찍_끝나도_stale_아님(lb_dir):
    """정상 배치가 정시보다 몇 분 일찍 끝난 경우 — 허용오차가 그 슬롯을 채운 것으로 본다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-29T06:57:00'))
    now = datetime(2026, 7, 29, 20, 0)         # 유예(13:00) 한참 경과
    assert store.get_freshness('us', now)['is_stale'] is False


def test_freshness_KR도_예정시각보다_일찍_끝나면_stale_아님(lb_dir):
    """KR 16:30 예정 → 16:28 완료. 다음날 오후까지 경고가 뜨면 안 된다."""
    _write(lb_dir, 'kr', _envelope([], source_updated_at='2026-07-29T16:28:00'))
    now = datetime(2026, 7, 30, 15, 0)         # 목요일, 직전 due는 수 16:30 + 6h 경과
    assert store.get_freshness('kr', now)['is_stale'] is False


def test_freshness_허용오차_밖으로_일찍_끝나면_슬롯_미충족(lb_dir):
    """허용오차(60분)보다 크게 벌어진 차이는 '일찍 끝남'이 아니라 미실행이다.

    US 07:00 예정인데 갱신 시각이 전날 05:00이면 어제·오늘 두 슬롯 모두 미충족.
    """
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-28T05:00:00'))
    now = datetime(2026, 7, 29, 9, 0)          # 유예 이전인데도 2슬롯이라 stale
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


def test_freshness_월요일_오후_이틀치_미실행이면_stale(lb_dir):
    """금·월 배치가 모두 안 돌았으면 목요일 갱신분은 두 슬롯(금·월)을 놓친 것이다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-30T07:02:16'))
    now = datetime(2026, 8, 3, 15, 0)          # 월요일 15:00 > 07:00+6h
    assert store.get_freshness('us', now)['is_stale'] is True


def test_freshness_kr은_1630_기준(lb_dir):
    """KR 예정시각 16:30 — 당일 12:00 조회 시 직전 due는 어제 16:30."""
    _write(lb_dir, 'kr', _envelope([], source_updated_at='2026-07-28T16:35:00'))
    now = datetime(2026, 7, 29, 12, 0)
    assert store.get_freshness('kr', now)['is_stale'] is False


def test_freshness_파일_없으면_시각없음_stale_아님(lb_dir):
    f = store.get_freshness('us', datetime(2026, 7, 29, 14, 0))
    assert f['has_timestamp'] is False
    assert f['is_stale'] is False


def test_freshness_갱신시각_없어도_숫자여도_예외_없음(lb_dir):
    """source_updated_at이 null이거나 숫자여도 탭이 죽으면 안 된다."""
    _write(lb_dir, 'us', _envelope([{'ticker': 'DELL'}], source_updated_at=None))
    assert store.get_freshness('us', datetime(2026, 7, 29, 14, 0))['has_timestamp'] is False

    _write(lb_dir, 'us', _envelope([{'ticker': 'DELL'}], source_updated_at=1753776136))
    f = store.get_freshness('us', datetime(2026, 7, 29, 14, 0))
    assert f['has_timestamp'] is False
    assert f['is_stale'] is False


def test_has_snapshot_파일_유무만_본다(lb_dir):
    """갱신 시각이 없어도 items가 있으면 '데이터 없음'이 아니다."""
    assert store.has_snapshot('us') is False

    _write(lb_dir, 'us', _envelope([{'ticker': 'DELL', 'rank': 1}],
                                   source_updated_at=None))
    assert store.has_snapshot('us') is True
    assert store.get_tickers('us') == {'DELL'}          # 배지와 탭이 어긋나면 안 된다


def test_has_snapshot_items_0개도_True(lb_dir):
    """0종목은 '주도주 부재'라는 정상 결과 — 데이터 없음과 구분한다."""
    _write(lb_dir, 'kr', _envelope([]))
    assert store.has_snapshot('kr') is True


def test_has_snapshot_깨진_JSON은_False(lb_dir):
    (lb_dir / 'us.json').write_text('{not json', encoding='utf-8')
    assert store.has_snapshot('us') is False


def test_freshness_now_생략시_KST_기준(lb_dir, monkeypatch):
    """컨테이너가 UTC여도 KST로 판정해야 한다 — now 생략 경로 검증."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-27T07:02:16'))

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC 05:00 = KST 14:00 (수요일) — 유예 경과 시점
            return datetime(2026, 7, 29, 5, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(store, 'datetime', _FakeDatetime)
    assert store.get_freshness('us')['is_stale'] is True


def test_freshness_UTC_aware_now도_KST로_해석(lb_dir):
    """aware datetime을 주면 KST로 변환해 비교한다."""
    _write(lb_dir, 'us', _envelope([], source_updated_at='2026-07-29T06:57:00'))
    now_utc = datetime(2026, 7, 29, 11, 0, tzinfo=timezone.utc)   # KST 20:00
    assert store.get_freshness('us', now_utc)['is_stale'] is False
