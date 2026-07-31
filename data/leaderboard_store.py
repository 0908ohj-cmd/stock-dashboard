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

# 시장별 예정 배치 시각 (KST)
_BATCH_DUE = {
    'us': (7, 0),
    'kr': (16, 30),
}
# 시장별 실행 요일 — 실제 배치 스케줄과 일치시킨 값이다.
# US는 주말 포함 매일 돌고, KR은 평일에만 돈다. 양쪽을 평일로 뭉뚱그리면
# 금요일에 죽은 US 파이프라인이 주말 내내 신선하다고 보고된다(약 54시간 사각지대).
_WEEKDAYS_ONLY = {
    'us': False,
    'kr': True,
}
# 배치 지연 흡수용 유예 시간 — 슬롯을 하나 놓친 동안은 이 시간 안에서 판정을 보류한다
_GRACE_HOURS = 6
# 예정 시각보다 이만큼 일찍 끝난 배치도 그 슬롯을 채운 것으로 본다.
# 상류 스케줄은 다른 저장소에 있어 예고 없이 몇 분 앞당겨질 수 있다.
_SLOT_TOLERANCE_MINUTES = 60
# 놓친 슬롯이 이 수 이상이면 유예와 무관하게 stale.
# 판정이 같아지므로 셀 때도 여기서 끊는다.
_STALE_MISSED = 2


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
    """정규화 봉투를 반환. 파일이 없거나 깨졌으면 빈 봉투 (예외 없음).

    items가 리스트가 아니거나(`null`·dict 등) 원소가 dict가 아니어도 예외를 내지 않는다.
    이 함수는 모든 와치리스트 탭 렌더 첫머리에서 try/except 없이 불리고 Streamlit은
    한 번의 스크립트 실행으로 모든 탭 본문을 돌리므로, 여기서 터지면 리더보드 탭만이
    아니라 코스피·코스닥·나스닥·10EMA 탭까지 통째로 비어 버린다.
    """
    data = _read(market)
    if data is None:
        return _empty(market)
    items = data.get('items')
    if not isinstance(items, list):        # null·dict·문자열 등은 '목록 없음'으로 본다
        items = []
    data['items'] = [it for it in items if isinstance(it, dict)]
    data['count'] = len(data['items'])     # 파일의 count를 믿지 않고 항상 재계산
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
    """교차 배지용 티커 집합. 파일이 없거나 형식이 깨졌으면 빈 집합 → 배지가 안 붙는다.

    원소가 dict가 아니거나 ticker가 문자열이 아니면 조용히 건너뛴다 — 배지 하나를
    포기하는 편이 와치리스트 탭 전체가 죽는 것보다 낫다.
    """
    tickers = set()
    for it in load(market).get('items', []):
        if not isinstance(it, dict):
            continue
        ticker = it.get('ticker')
        if isinstance(ticker, str) and ticker:
            tickers.add(ticker)
    return tickers


def _as_kst(dt: datetime) -> datetime:
    """naive면 KST로 간주하고, aware면 KST로 변환한다.

    소스 갱신 시각은 배치 실행 머신의 naive KST 문자열이다 — 컨테이너 로컬시각이 아니다.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _is_slot_day(market: str, dt: datetime) -> bool:
    """그 날짜에 이 시장의 배치가 도는가 (5=토, 6=일)."""
    if _WEEKDAYS_ONLY.get(market, True):
        return dt.weekday() < 5
    return True


def _last_due(market: str, now: datetime) -> datetime:
    """지금 기준 가장 최근에 지나간 예정 배치 시각 (시장별 실행 요일 반영)."""
    hh, mm = _BATCH_DUE[market]
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if due > now:
        due -= timedelta(days=1)
    while not _is_slot_day(market, due):   # 주말 배치가 없는 시장만 되감는다
        due -= timedelta(days=1)
    return due


def _prev_due(market: str, due: datetime) -> datetime:
    """한 슬롯 앞(그 시장의 직전 실행일)의 예정 배치 시각."""
    due -= timedelta(days=1)
    while not _is_slot_day(market, due):
        due -= timedelta(days=1)
    return due


def _missed_slots(market: str, updated: datetime, now: datetime) -> int:
    """갱신 시각 이후로 지나갔지만 채워지지 않은 슬롯 수 (_STALE_MISSED에서 절단).

    슬롯 S는 `updated >= S - 허용오차`면 채워진 것으로 본다. 허용오차가 없으면
    정시보다 몇 분 일찍 끝난 정상 배치가 매일 미실행으로 잡힌다.
    """
    tolerance = timedelta(minutes=_SLOT_TOLERANCE_MINUTES)
    due = _last_due(market, now)
    missed = 0
    while missed < _STALE_MISSED and due - tolerance > updated:
        missed += 1
        due = _prev_due(market, due)
    return missed


def get_freshness(market: str, now: datetime | None = None) -> dict:
    """신선도 판정 (모든 비교는 KST 기준).

    갱신 시각 이후 '놓친 슬롯 수'로 판정한다 (슬롯 요일은 시장별로 다르다 —
    US 매일 07:00, KR 평일 16:30):

    - 0개 → 신선
    - 1개 + 직전 예정시각 + 유예 이내 → 판정 보류(신선). 오늘 배치가 늦게 돌고
      있을 수 있으므로 아직 경고하지 않는다
    - 1개 + 유예 경과 → stale
    - 2개 이상 → 유예와 무관하게 stale. 며칠째 멈춘 파이프라인이 매일 아침 6시간씩
      신선하다고 보고되는 사각지대를 없앤다

    슬롯 충족 판정에 허용오차(_SLOT_TOLERANCE_MINUTES)를 둬서, 정시보다 조금 일찍
    끝난 배치가 오탐되지 않게 한다 — 하루치 감지를 포기하지 않고 같은 목적을 이룬다.

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
    missed = _missed_slots(market, updated, now)
    past_grace = now > _last_due(market, now) + timedelta(hours=_GRACE_HOURS)
    is_stale = missed >= _STALE_MISSED or (missed == 1 and past_grace)
    return {'source_updated_at': src, 'synced_at': data.get('synced_at'),
            'is_stale': is_stale, 'has_timestamp': True}
