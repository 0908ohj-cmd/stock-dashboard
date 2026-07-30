"""리더보드 JSON 읽기 — 정규화된 스냅샷 로드·티커 집합·신선도 판정.

Streamlit 무의존 (캐시가 필요하면 ui/ 계층에서 래핑).
쓰기는 scripts/sync_leaderboard.py 담당이고 이 모듈은 읽기 전용이다.
"""
import json
import pathlib
from datetime import datetime, timedelta

LEADERBOARD_DIR = pathlib.Path(__file__).parent / 'leaderboard'

# 시장별 예정 배치 시각 (KST, 평일 기준)
_BATCH_DUE = {
    'us': (7, 0),
    'kr': (16, 30),
}
# 배치 지연 흡수용 유예 시간 — 이 시간 안에는 stale 판정을 보류한다
_GRACE_HOURS = 6


def _empty(market: str) -> dict:
    return {
        'market': market.upper(),
        'synced_at': None,
        'source_updated_at': None,
        'count': 0,
        'items': [],
    }


def load(market: str) -> dict:
    """정규화 봉투를 반환. 파일이 없거나 깨졌으면 빈 봉투 (예외 없음)."""
    path = LEADERBOARD_DIR / f'{market}.json'
    if not path.exists():
        return _empty(market)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return _empty(market)
    if not isinstance(data, dict):
        return _empty(market)
    data.setdefault('items', [])
    data.setdefault('count', len(data['items']))
    data.setdefault('source_updated_at', None)
    data.setdefault('synced_at', None)
    return data


def get_tickers(market: str) -> set:
    """교차 배지용 티커 집합. 파일이 없으면 빈 집합 → 배지가 안 붙는다."""
    return {it['ticker'] for it in load(market).get('items', []) if it.get('ticker')}


def _last_due(market: str, now: datetime) -> datetime:
    """지금 기준 가장 최근에 지나간 평일 예정 배치 시각."""
    hh, mm = _BATCH_DUE[market]
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if due > now:
        due -= timedelta(days=1)
    while due.weekday() >= 5:          # 5=토, 6=일 — 주말엔 배치가 없다
        due -= timedelta(days=1)
    return due


def get_freshness(market: str, now: datetime | None = None) -> dict:
    """신선도 판정.

    stale = (지금 > 직전 예정시각 + 유예) AND (갱신시각 < 직전 예정시각)
    두 조건이 모두 필요하다. 앞 조건이 없으면 정시 성공 직후에도 오판한다.
    """
    now = now or datetime.now()
    data = load(market)
    src = data.get('source_updated_at')
    if not src:
        return {'source_updated_at': None, 'synced_at': data.get('synced_at'),
                'is_stale': False, 'has_data': False}
    try:
        updated = datetime.fromisoformat(src)
    except ValueError:
        return {'source_updated_at': src, 'synced_at': data.get('synced_at'),
                'is_stale': False, 'has_data': False}
    if updated.tzinfo is not None:      # naive 비교로 통일
        updated = updated.replace(tzinfo=None)
    due = _last_due(market, now)
    is_stale = now > due + timedelta(hours=_GRACE_HOURS) and updated < due
    return {'source_updated_at': src, 'synced_at': data.get('synced_at'),
            'is_stale': is_stale, 'has_data': True}
