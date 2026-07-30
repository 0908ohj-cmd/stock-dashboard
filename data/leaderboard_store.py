"""리더보드 JSON 읽기 — 정규화된 스냅샷 로드·티커 집합·신선도 판정.

Streamlit 무의존 (캐시가 필요하면 ui/ 계층에서 래핑).
쓰기는 scripts/sync_leaderboard.py 담당이고 이 모듈은 읽기 전용이다.
"""
import json
import pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LEADERBOARD_DIR = pathlib.Path(__file__).parent / 'leaderboard'

# 배치 시각·소스 갱신 시각 모두 KST 기준이다.
# 배포 컨테이너(Streamlit Cloud)는 UTC라서 datetime.now()를 그대로 쓰면 9시간 어긋난다.
KST = ZoneInfo('Asia/Seoul')

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


def _read(market: str) -> dict | None:
    """파일을 읽어 봉투 dict를 반환. 파일이 없거나 깨졌으면 None."""
    path = LEADERBOARD_DIR / f'{market}.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def load(market: str) -> dict:
    """정규화 봉투를 반환. 파일이 없거나 깨졌으면 빈 봉투 (예외 없음)."""
    data = _read(market)
    if data is None:
        return _empty(market)
    data.setdefault('items', [])
    data.setdefault('count', len(data['items']))
    data.setdefault('source_updated_at', None)
    data.setdefault('synced_at', None)
    return data


def has_snapshot(market: str) -> bool:
    """읽을 수 있는 스냅샷 파일이 있는지.

    items가 0개여도 True — '주도주 부재(정상 결과 0개)'와 '데이터 없음(장애)'은
    화면 문구가 다른 별개 상태다(§7). 갱신 시각 유무와는 무관하다.
    """
    return _read(market) is not None


def get_tickers(market: str) -> set:
    """교차 배지용 티커 집합. 파일이 없으면 빈 집합 → 배지가 안 붙는다."""
    return {it['ticker'] for it in load(market).get('items', []) if it.get('ticker')}


def _as_kst(dt: datetime) -> datetime:
    """naive면 KST로 간주하고, aware면 KST로 변환한다.

    소스 갱신 시각은 배치 실행 머신의 naive KST 문자열이다 — 컨테이너 로컬시각이 아니다.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _nth_last_due(market: str, now: datetime, n: int = 1) -> datetime:
    """지금 기준 n번째로 최근에 지나간 평일 예정 배치 시각 (n=1이 직전 슬롯)."""
    hh, mm = _BATCH_DUE[market]
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if due > now:
        due -= timedelta(days=1)
    while due.weekday() >= 5:              # 5=토, 6=일 — 주말엔 배치가 없다
        due -= timedelta(days=1)
    for _ in range(n - 1):
        due -= timedelta(days=1)
        while due.weekday() >= 5:
            due -= timedelta(days=1)
    return due


def get_freshness(market: str, now: datetime | None = None) -> dict:
    """신선도 판정 (모든 비교는 KST 기준).

    stale = (지금 > 직전 예정시각 + 유예) AND (갱신시각 < 전전 예정시각)

    앞 조건이 없으면 정시 성공 직후에도 오판한다.
    뒤 조건에서 '전전'을 쓰는 이유: 상류 배치 스케줄은 다른 저장소에 있어 예고 없이
    앞당겨질 수 있다. '직전' 기준이면 정시보다 몇 분 일찍 끝난 정상 데이터가 매일
    지연으로 잡힌다. 감지가 하루 늦어지는 대신 스케줄 드리프트에 면역이 되는 쪽을 택했다.

    has_timestamp는 '표시할 수 있는 갱신 시각이 있는가'만 뜻한다 —
    데이터 유무 판단에 쓰면 안 된다(items가 있어도 시각만 없을 수 있다).
    """
    now = _as_kst(now) if now is not None else datetime.now(KST)
    data = load(market)
    src = data.get('source_updated_at')
    base = {'source_updated_at': src, 'synced_at': data.get('synced_at'),
            'is_stale': False, 'has_timestamp': False}
    if not src:
        return base
    try:
        updated = _as_kst(datetime.fromisoformat(src))
    except (ValueError, TypeError):        # 문자열이 아니거나 ISO 형식이 아닌 경우
        return base
    last_due = _nth_last_due(market, now, 1)
    prev_due = _nth_last_due(market, now, 2)
    is_stale = now > last_due + timedelta(hours=_GRACE_HOURS) and updated < prev_due
    return {'source_updated_at': src, 'synced_at': data.get('synced_at'),
            'is_stale': is_stale, 'has_timestamp': True}
