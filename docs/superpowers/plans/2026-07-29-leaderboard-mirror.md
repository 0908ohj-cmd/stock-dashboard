# 리더보드 미러링 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 외부 파이프라인이 산출한 US/KR 리더보드를 JSON으로 미러링해, 신규 탭에서 열람하고 기존 와치리스트 행에 👑 교차 배지로 표시한다.

**Architecture:** 배치가 소스 JSON을 공통 스키마로 정규화해 GitHub Contents API로 `data/leaderboard/{us,kr}.json`에 푸시한다. 앱은 그 파일만 읽는다 — 네트워크 수집 없음. `data/leaderboard_store.py`(읽기, Streamlit 무의존) → `ui/leaderboard.py`(렌더)의 기존 3계층 구조를 따른다.

**Tech Stack:** Python 3.11, pandas, Streamlit, streamlit-aggrid, requests, pytest

## Global Constraints

- **UI 문구·에러 메시지·컬럼명 어디에도 소스 파이프라인 이름(stockEdge)을 노출하지 않는다.** 전부 "리더보드"로 부른다. 소스 경로를 아는 유일한 파일은 `scripts/sync_leaderboard.py`다
- `data/`·`scripts/` 계층은 **Streamlit을 import하지 않는다** (단위 테스트 가능성 유지)
- UI 라벨·주석·커밋 메시지는 **한국어**. 커밋 접두사는 `feat:` / `fix:` / `docs:` / `refactor:`
- 테스트 실행은 항상 `--ignore=tests/test_scoring.py` 를 붙인다 (스테일 파일이라 수집이 깨진다)
- 시장 코드는 `data/`·`scripts/` 계층에서 **소문자 `'us'` / `'kr'`** 로 통일한다. UI 계층의 `'KR_KOSPI'`/`'KR_KOSDAQ'`/`'US'` 는 경계에서 매핑한다
- 공통 스키마 아이템의 **키 구성은 US·KR이 완전히 동일**해야 한다. 소스에 없는 값은 `null`(문자열은 `""`)로 채운다
- 리더보드 `items`가 0개인 것은 정상 상태다(주도주 부재 신호). 장애로 취급해 이전 데이터로 덮지 않는다

---

### Task 1: 리더보드 읽기 모듈

**Files:**
- Create: `data/leaderboard_store.py`
- Test: `tests/test_leaderboard_store.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `load(market: str) -> dict` — 정규화 봉투 반환. 파일 없으면 빈 봉투
  - `get_tickers(market: str) -> set[str]` — 티커 집합. 파일 없으면 `set()`
  - `get_freshness(market: str, now: datetime | None = None) -> dict` — `{'source_updated_at': str|None, 'synced_at': str|None, 'is_stale': bool, 'has_data': bool}`
  - 모듈 상수 `LEADERBOARD_DIR: pathlib.Path`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_leaderboard_store.py` 생성:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_leaderboard_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.leaderboard_store'`

- [ ] **Step 3: 최소 구현 작성**

`data/leaderboard_store.py` 생성:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_leaderboard_store.py -v`
Expected: PASS (13개 전부)

- [ ] **Step 5: 커밋**

```bash
git add data/leaderboard_store.py tests/test_leaderboard_store.py
git commit -m "feat: 리더보드 읽기 모듈 추가 — 로드·티커집합·신선도 판정"
```

---

### Task 2: 소스 정규화 로직

**Files:**
- Create: `scripts/sync_leaderboard.py` (정규화 함수만 — CLI·푸시는 Task 3)
- Test: `tests/test_sync_leaderboard.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `COMMON_KEYS: list[str]` — 공통 아이템 키 순서
  - `normalize_us(payload: dict) -> list[dict]`
  - `normalize_kr(payload: dict) -> list[dict]`
  - `sort_and_rank(items: list[dict]) -> list[dict]` — 정렬 후 `rank` 1..N 부여
  - `build_envelope(market: str, items: list[dict], source_updated_at: str | None) -> dict`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sync_leaderboard.py` 생성:

```python
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))

import sync_leaderboard as sync


US_SOURCE = {
    'updated_at': '2026-07-29T07:02:16',
    'items': [
        {
            'ticker': 'DELL', 'sources': ['leaders', 'leader_ride'],
            'added_at': '2026-07-03', 'last_seen': '2026-07-29',
            'close': 426.91, 'perf_1m': 4.4, 'perf_3m': 97.9,
            'perf_6m': 271.6, 'perf_12m': 236.9, 'rs_rating': 96,
            'weighted_perf': 0.5563, 'dist_from_52w_high': -8.2,
            'dist_from_52w_low': 286.2, 'dollar_vol_w': 18879041930,
            'adr': 7.4, 'base_len_argmax': 38, 'theme': 'AI 서버 인프라',
        },
    ],
}

KR_SOURCE = {
    'updated_at': '2026-07-29T16:36:40',
    'items': [
        {
            'rank': 1, 'ticker': '005930', 'name': '삼성전자',
            'market': 'KOSPI', 'sources': ['leaders'], 'sector': '반도체',
            'close': 71000, 'market_cap': 420000000000000,
            'avg_dollar_vol': 850000000000, 'adr': 2.4,
            'dist_from_52w_high': -5.1, 'dist_from_52w_low': 42.0,
            'perf_1m': 3.2, 'perf_3m': 18.0, 'perf_6m': 30.5,
            'perf_12m': 41.2, 'rs_rating': 88, 'theme': '반도체',
        },
    ],
}


def test_normalize_us_주간거래대금을_일평균으로_환산():
    items = sync.normalize_us(US_SOURCE)
    assert items[0]['avg_dollar_vol'] == round(18879041930 / 5)


def test_normalize_us_이름은_빈문자열_섹터는_null():
    items = sync.normalize_us(US_SOURCE)
    assert items[0]['name'] == ''
    assert items[0]['sector'] is None
    assert items[0]['market'] == 'US'


def test_normalize_us_필드_보존():
    items = sync.normalize_us(US_SOURCE)
    it = items[0]
    assert it['ticker'] == 'DELL'
    assert it['rs_rating'] == 96
    assert it['adr'] == 7.4
    assert it['added_at'] == '2026-07-03'
    assert it['theme'] == 'AI 서버 인프라'
    assert it['sources'] == ['leaders', 'leader_ride']


def test_normalize_kr_이름_섹터_유지_added_at은_null():
    items = sync.normalize_kr(KR_SOURCE)
    it = items[0]
    assert it['name'] == '삼성전자'
    assert it['sector'] == '반도체'
    assert it['market'] == 'KOSPI'
    assert it['added_at'] is None


def test_normalize_kr_거래대금은_그대로():
    items = sync.normalize_kr(KR_SOURCE)
    assert items[0]['avg_dollar_vol'] == 850000000000


def test_양_시장_키_구성이_동일():
    us_keys = set(sync.normalize_us(US_SOURCE)[0].keys())
    kr_keys = set(sync.normalize_kr(KR_SOURCE)[0].keys())
    assert us_keys == kr_keys == set(sync.COMMON_KEYS)


def test_sort_and_rank_leaders_우선_그다음_RS내림차순():
    items = [
        {'ticker': 'A', 'sources': ['leader_ride'], 'rs_rating': 99, 'avg_dollar_vol': 100},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 85, 'avg_dollar_vol': 100},
        {'ticker': 'C', 'sources': ['leaders'], 'rs_rating': 92, 'avg_dollar_vol': 100},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['C', 'B', 'A']
    assert [it['rank'] for it in ranked] == [1, 2, 3]


def test_sort_and_rank_RS동률이면_거래대금_큰순():
    items = [
        {'ticker': 'A', 'sources': ['leaders'], 'rs_rating': 90, 'avg_dollar_vol': 100},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 90, 'avg_dollar_vol': 500},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['B', 'A']


def test_sort_and_rank_null값은_뒤로():
    items = [
        {'ticker': 'A', 'sources': ['leaders'], 'rs_rating': None, 'avg_dollar_vol': None},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 70, 'avg_dollar_vol': 10},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['B', 'A']


def test_kr_소스의_기존_rank는_무시하고_재부여():
    payload = {'updated_at': 'x', 'items': [
        dict(KR_SOURCE['items'][0], rank=9, ticker='005930', rs_rating=80),
        dict(KR_SOURCE['items'][0], rank=1, ticker='000660', rs_rating=95),
    ]}
    ranked = sync.sort_and_rank(sync.normalize_kr(payload))
    assert ranked[0]['ticker'] == '000660'
    assert ranked[0]['rank'] == 1


def test_build_envelope():
    items = sync.sort_and_rank(sync.normalize_us(US_SOURCE))
    env = sync.build_envelope('us', items, US_SOURCE['updated_at'])
    assert env['market'] == 'US'
    assert env['count'] == 1
    assert env['source_updated_at'] == '2026-07-29T07:02:16'
    assert env['synced_at']
    assert env['items'][0]['ticker'] == 'DELL'


def test_build_envelope_빈_리스트도_정상():
    env = sync.build_envelope('kr', [], '2026-07-29T16:36:40')
    assert env['count'] == 0
    assert env['items'] == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_sync_leaderboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sync_leaderboard'`

- [ ] **Step 3: 최소 구현 작성**

`scripts/sync_leaderboard.py` 생성 (이 태스크에서는 정규화 부분만):

```python
#!/usr/bin/env python3
"""리더보드 소스 JSON을 공통 스키마로 정규화해 대시보드 repo에 푸시한다.

이 repo에서 소스 경로를 아는 유일한 파일. 앱 코드는 data/leaderboard/*.json만 안다.
Streamlit 무의존.
"""
from datetime import datetime

# 공통 아이템 키 — US·KR 정규화 결과가 이 구성을 완전히 동일하게 갖는다
COMMON_KEYS = [
    'rank', 'ticker', 'name', 'market', 'sources', 'added_at', 'close',
    'rs_rating', 'adr', 'perf_1m', 'perf_3m', 'perf_6m', 'perf_12m',
    'dist_from_52w_high', 'avg_dollar_vol', 'theme', 'sector',
]


def _common(it: dict, *, name: str, market: str, added_at, avg_dollar_vol, sector) -> dict:
    """시장별 차이를 인자로 받고 나머지 공통 필드를 채운다."""
    return {
        'rank': None,                     # sort_and_rank가 부여
        'ticker': it.get('ticker'),
        'name': name,
        'market': market,
        'sources': it.get('sources') or [],
        'added_at': added_at,
        'close': it.get('close'),
        'rs_rating': it.get('rs_rating'),
        'adr': it.get('adr'),
        'perf_1m': it.get('perf_1m'),
        'perf_3m': it.get('perf_3m'),
        'perf_6m': it.get('perf_6m'),
        'perf_12m': it.get('perf_12m'),
        'dist_from_52w_high': it.get('dist_from_52w_high'),
        'avg_dollar_vol': avg_dollar_vol,
        'theme': it.get('theme') or '',
        'sector': sector,
    }


def normalize_us(payload: dict) -> list:
    """US 소스 → 공통 스키마. 주간 거래대금을 일평균으로 환산한다."""
    out = []
    for it in payload.get('items', []):
        dvw = it.get('dollar_vol_w')
        out.append(_common(
            it,
            name='',                                    # 소스에 종목명 없음
            market='US',
            added_at=it.get('added_at'),
            avg_dollar_vol=round(dvw / 5) if dvw else None,
            sector=None,
        ))
    return out


def normalize_kr(payload: dict) -> list:
    """KR 소스 → 공통 스키마. 거래대금은 이미 일평균이라 그대로 쓴다."""
    out = []
    for it in payload.get('items', []):
        out.append(_common(
            it,
            name=it.get('name') or '',
            market=it.get('market') or 'KR',
            added_at=None,                              # 소스에 편입일 없음
            avg_dollar_vol=it.get('avg_dollar_vol'),
            sector=it.get('sector'),
        ))
    return out


def sort_and_rank(items: list) -> list:
    """leaders 우선 → RS 내림차순 → 거래대금 내림차순. rank 1..N 부여.

    양 시장 동일 규칙이라 소스에 rank가 있어도 무시하고 재계산한다.
    """
    ordered = sorted(items, key=lambda it: (
        0 if 'leaders' in (it.get('sources') or []) else 1,
        -(it['rs_rating'] if it.get('rs_rating') is not None else -1),
        -(it.get('avg_dollar_vol') or 0),
    ))
    for i, it in enumerate(ordered, start=1):
        it['rank'] = i
    return ordered


def build_envelope(market: str, items: list, source_updated_at) -> dict:
    return {
        'market': market.upper(),
        'synced_at': datetime.now().isoformat(timespec='seconds'),
        'source_updated_at': source_updated_at,
        'count': len(items),
        'items': items,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_sync_leaderboard.py -v`
Expected: PASS (12개 전부)

- [ ] **Step 5: 커밋**

```bash
git add scripts/sync_leaderboard.py tests/test_sync_leaderboard.py
git commit -m "feat: 리더보드 소스 정규화 로직 — US/KR 공통 스키마 변환"
```

---

### Task 3: sync CLI + GitHub 푸시

**Files:**
- Modify: `scripts/sync_leaderboard.py` (Task 2 파일에 CLI·푸시 추가)
- Modify: `tests/test_sync_leaderboard.py` (테스트 추가)
- Modify: `requirements.txt` (`requests` 명시)

> 참고: `app.py`가 이미 `requests`를 쓰고 있지만 `requirements.txt`에는 빠져 있다 (streamlit이 간접 의존으로 끌어와 동작해 온 상태). sync 스크립트도 쓰므로 이 태스크에서 명시적으로 추가한다.

**Interfaces:**
- Consumes: Task 2의 `normalize_us`, `normalize_kr`, `sort_and_rank`, `build_envelope`
- Produces:
  - `get_token() -> str` — `DASHBOARD_GITHUB_TOKEN` → `gh auth token` 순 조회
  - `load_source(market: str, source_dir: pathlib.Path) -> dict` — 소스 JSON 로드 (없으면 `FileNotFoundError`)
  - `sync_market(market, source_dir, local_only, token) -> dict` — 한 시장 처리, 봉투 반환
  - `main(argv=None) -> int` — 종료 코드 반환

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sync_leaderboard.py` 끝에 추가:

```python
import json
import os
import pytest


def test_load_source_us(tmp_path):
    (tmp_path / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    payload = sync.load_source('us', tmp_path)
    assert payload['updated_at'] == '2026-07-29T07:02:16'


def test_load_source_kr_파일명(tmp_path):
    (tmp_path / 'leaderboard_kr.json').write_text(
        json.dumps(KR_SOURCE, ensure_ascii=False), encoding='utf-8')
    payload = sync.load_source('kr', tmp_path)
    assert payload['items'][0]['ticker'] == '005930'


def test_load_source_파일_없으면_예외(tmp_path):
    with pytest.raises(FileNotFoundError):
        sync.load_source('us', tmp_path)


def test_sync_market_local_only_파일_생성(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)

    env = sync.sync_market('us', src, local_only=True, token=None)

    written = json.loads((out / 'us.json').read_text(encoding='utf-8'))
    assert written['count'] == 1
    assert written['items'][0]['rank'] == 1
    assert env['count'] == 1


def test_sync_market_빈_items도_정상_푸시(tmp_path, monkeypatch):
    """0개는 주도주 부재 신호 — 장애가 아니므로 그대로 쓴다."""
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard_kr.json').write_text(
        json.dumps({'updated_at': '2026-07-29T16:36:40', 'items': []}),
        encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)

    sync.sync_market('kr', src, local_only=True, token=None)

    written = json.loads((out / 'kr.json').read_text(encoding='utf-8'))
    assert written['count'] == 0
    assert written['items'] == []


def test_main_소스_없으면_exit1_푸시_안함(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, 'push_to_github', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')
    rc = sync.main(['--market', 'us', '--source-dir', str(tmp_path)])
    assert rc == 1
    assert calls == []


def test_main_all_한쪽_실패해도_다른쪽은_푸시(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    # KR 소스는 일부러 만들지 않는다
    pushed = []
    monkeypatch.setattr(sync, 'push_to_github',
                        lambda market, env, token: pushed.append(market))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')

    rc = sync.main(['--market', 'all', '--source-dir', str(src)])

    assert rc == 1              # KR 실패
    assert pushed == ['us']     # US는 정상 푸시


def test_get_token_환경변수_우선(monkeypatch):
    monkeypatch.setenv('DASHBOARD_GITHUB_TOKEN', 'env-token')
    assert sync.get_token() == 'env-token'
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python3 -m pytest tests/test_sync_leaderboard.py -v -k "load_source or sync_market or main or get_token"`
Expected: FAIL — `AttributeError: module 'sync_leaderboard' has no attribute 'load_source'`

- [ ] **Step 3: 최소 구현 작성**

`scripts/sync_leaderboard.py` 상단 import를 다음으로 교체:

```python
import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime

import requests

GITHUB_REPO = '0908ohj-cmd/stock-dashboard'
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'leaderboard'

# 소스 경로 — 이 repo에서 외부 파이프라인 위치를 아는 유일한 지점
DEFAULT_SOURCE_DIR = pathlib.Path.home() / 'Workspace' / 'stockEdge' / 'data'
_SOURCE_FILENAME = {'us': 'leaderboard.json', 'kr': 'leaderboard_kr.json'}
```

그리고 `build_envelope` 아래에 추가:

```python
def load_source(market: str, source_dir) -> dict:
    """소스 JSON 로드. 없으면 FileNotFoundError — 호출부가 푸시를 건너뛴다."""
    path = pathlib.Path(source_dir) / _SOURCE_FILENAME[market]
    if not path.exists():
        raise FileNotFoundError(f'리더보드 소스를 찾을 수 없습니다: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def get_token() -> str:
    """DASHBOARD_GITHUB_TOKEN → gh auth token 순으로 조회."""
    token = os.environ.get('DASHBOARD_GITHUB_TOKEN', '').strip()
    if token:
        return token
    try:
        out = subprocess.run(['gh', 'auth', 'token'], capture_output=True,
                             text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    raise RuntimeError(
        'GitHub 토큰을 찾을 수 없습니다. '
        'DASHBOARD_GITHUB_TOKEN 환경변수를 설정하거나 gh CLI로 로그인하세요.')


def push_to_github(market: str, envelope: dict, token: str) -> None:
    """Contents API로 data/leaderboard/{market}.json 갱신 (sha 조회 후 update)."""
    path = f'data/leaderboard/{market}.json'
    url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{path}'
    hdrs = {'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json'}
    r = requests.get(url, headers=hdrs, timeout=10)
    sha = r.json().get('sha') if r.ok else None
    content = json.dumps(envelope, ensure_ascii=False, indent=2)
    body = {'message': f'data: 리더보드 {market.upper()} {envelope["count"]}종목',
            'content': base64.b64encode(content.encode()).decode()}
    if sha:
        body['sha'] = sha
    resp = requests.put(url, json=body, headers=hdrs, timeout=20)
    if not resp.ok:
        raise RuntimeError(f'GitHub 푸시 실패 ({resp.status_code}): {resp.text[:200]}')


def sync_market(market: str, source_dir, local_only: bool, token) -> dict:
    """한 시장을 정규화해 로컬 저장 또는 GitHub 푸시."""
    payload = load_source(market, source_dir)
    normalize = normalize_us if market == 'us' else normalize_kr
    items = sort_and_rank(normalize(payload))
    envelope = build_envelope(market, items, payload.get('updated_at'))

    if local_only:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / f'{market}.json').write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        push_to_github(market, envelope, token)
    return envelope


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='리더보드 동기화')
    ap.add_argument('--market', choices=['us', 'kr', 'all'], default='all')
    ap.add_argument('--source-dir', default=None)
    ap.add_argument('--local-only', action='store_true',
                    help='GitHub에 푸시하지 않고 로컬 파일만 갱신 (개발·초기 생성용)')
    args = ap.parse_args(argv)

    source_dir = pathlib.Path(
        args.source_dir
        or os.environ.get('LEADERBOARD_SOURCE_DIR')
        or DEFAULT_SOURCE_DIR)

    markets = ['us', 'kr'] if args.market == 'all' else [args.market]
    token = None if args.local_only else get_token()

    failed = []
    for market in markets:
        try:
            env = sync_market(market, source_dir, args.local_only, token)
            print(f'[{market}] {env["count"]}종목 · 소스 갱신 {env["source_updated_at"]}')
        except Exception as e:
            print(f'[{market}] 실패: {e}', file=sys.stderr)
            failed.append(market)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: requirements.txt에 requests 추가**

`requirements.txt`의 `lxml` 줄 앞에 추가:

```
requests
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_sync_leaderboard.py -v`
Expected: PASS (20개 전부)

- [ ] **Step 6: 실제 소스로 로컬 생성**

Run: `python3 scripts/sync_leaderboard.py --market all --local-only`
Expected: `[us] 20종목 ...` / `[kr] 0종목 ...` 출력, `data/leaderboard/us.json`·`kr.json` 생성

생성된 파일 확인:
```bash
python3 -c "
import json
for m in ('us','kr'):
    d = json.load(open(f'data/leaderboard/{m}.json'))
    print(m, d['count'], d['source_updated_at'])
    if d['items']: print(' ', d['items'][0]['rank'], d['items'][0]['ticker'], d['items'][0]['rs_rating'])
"
```

- [ ] **Step 7: 커밋**

```bash
git add scripts/sync_leaderboard.py tests/test_sync_leaderboard.py data/leaderboard/ requirements.txt
git commit -m "feat: 리더보드 sync CLI + GitHub 푸시, 초기 스냅샷 생성"
```

---

### Task 4: 리더보드 탭

**Files:**
- Create: `ui/leaderboard.py`
- Modify: `app.py:151-165` (탭 목록에 추가)

**Interfaces:**
- Consumes: Task 1의 `data.leaderboard_store.load`, `get_freshness`
- Produces: `render_leaderboard_tab() -> None`

- [ ] **Step 1: 렌더 모듈 작성**

`ui/leaderboard.py` 생성:

```python
"""👑 리더보드 탭 — 시장 주도주 명단 열람.

data/leaderboard/*.json만 읽는다 (네트워크 수집 없음).
"""
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

from data import leaderboard_store

_SOURCE_LABEL = {
    'leaders': '리더',
    'leader_ride': '리더 10EMA',
}


def _fmt_sources(sources) -> str:
    return ', '.join(_SOURCE_LABEL.get(s, s) for s in (sources or []))


def _fmt_updated(iso: str) -> str:
    """'2026-07-29T07:02:16' → '07-29 07:02'"""
    if not iso:
        return '—'
    try:
        return f'{iso[5:7]}-{iso[8:10]} {iso[11:16]}'
    except Exception:
        return iso


def render_leaderboard_tab():
    st.markdown('#### 👑 리더보드')
    st.caption('시장 전체에서 RS 상위 주도주를 매일 추려낸 명단입니다.')

    choice = st.radio('시장', ['🇺🇸 US', '🇰🇷 KR'],
                      horizontal=True, key='lb_market', label_visibility='collapsed')
    market = 'us' if 'US' in choice else 'kr'

    data = leaderboard_store.load(market)
    fresh = leaderboard_store.get_freshness(market)
    items = data.get('items', [])

    if not fresh['has_data']:
        st.info('리더보드 데이터가 아직 없습니다.')
        return

    c1, c2 = st.columns([3, 1])
    c1.caption(f"📅 {_fmt_updated(data.get('source_updated_at'))} 갱신 · {len(items)}종목")
    if fresh['is_stale']:
        c2.warning('갱신 지연', icon='⚠️')

    if not items:
        st.info('조건을 충족한 종목이 없습니다 — 주도주 부재 신호일 수 있습니다.')
        return

    is_kr = market == 'kr'
    display_df = pd.DataFrame([{
        '#':        it['rank'],
        '티커 | 종목명': f"{it['ticker']} | {it['name']}" if it.get('name') else it['ticker'],
        '소스':      _fmt_sources(it.get('sources')),
        '테마':      it.get('theme') or '기타',
        '종가':      it.get('close'),
        'RS':        it.get('rs_rating'),
        'ADR%':      it.get('adr'),
        '1M%':       it.get('perf_1m'),
        '3M%':       it.get('perf_3m'),
        '6M%':       it.get('perf_6m'),
        '12M%':      it.get('perf_12m'),
        '52H%':      it.get('dist_from_52w_high'),
        '거래대금':   it.get('avg_dollar_vol'),
    } for it in items])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True,
                                floatingFilter=True, minWidth=70)

    close_fmt = ("value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
                 if is_kr else "value == null ? '' : '$' + value.toFixed(2)")
    vol_fmt = JsCode("""
function(params) {
    const v = params.value;
    if (v == null) return '';
    if (%s) {
        if (v >= 1e12) return (v/1e12).toFixed(1) + '조';
        if (v >= 1e8)  return Math.round(v/1e8).toLocaleString('ko-KR') + '억';
        return Math.round(v).toLocaleString('ko-KR');
    }
    if (v >= 1e9) return '$' + (v/1e9).toFixed(1) + 'B';
    if (v >= 1e6) return '$' + Math.round(v/1e6) + 'M';
    return '$' + Math.round(v).toLocaleString();
}
""" % ('true' if is_kr else 'false'))
    rs_style = JsCode("""
function(params) {
    if (params.value == null) return {};
    return params.value >= 80
        ? {color: '#00E676', fontWeight: 'bold'}
        : {};
}
""")

    gb.configure_column('#', type=['numericColumn'], maxWidth=70)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter',
                        pinned='left', minWidth=160, flex=2)
    gb.configure_column('소스', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('테마', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('종가', filter='agNumberColumnFilter', type=['numericColumn'],
                        valueFormatter=close_fmt, flex=1)
    gb.configure_column('RS', filter='agNumberColumnFilter', type=['numericColumn'],
                        cellStyle=rs_style, maxWidth=90)
    for col in ('ADR%', '1M%', '3M%', '6M%', '12M%', '52H%'):
        gb.configure_column(col, filter='agNumberColumnFilter',
                            type=['numericColumn'], flex=1)
    gb.configure_column('거래대금', filter='agNumberColumnFilter',
                        type=['numericColumn'], valueFormatter=vol_fmt, flex=1)
    gb.configure_grid_options(domLayout='autoHeight', rowHeight=28)

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
        theme='streamlit',
        fit_columns_on_grid_load=True,
    )
```

- [ ] **Step 2: app.py에 탭 추가**

`app.py:6-9` import 블록에 추가:

```python
from ui.leaderboard import render_leaderboard_tab
```

`app.py:151-165`를 다음으로 교체:

```python
tab_kospi, tab_kosdaq, tab_us, tab_10ema_kospi, tab_10ema_kosdaq, tab_10ema_us, tab_lb = st.tabs([
    '🇰🇷 코스피', '🇰🇷 코스닥', '🇺🇸 나스닥',
    '📈 10EMA 코스피', '📈 10EMA 코스닥', '📈 10EMA 나스닥', '👑 리더보드'
])
with tab_kospi:
    render_watchlist_tab(kr_kospi, 'KR_KOSPI', 'KOSPI')
with tab_kosdaq:
    render_watchlist_tab(kr_kosdaq, 'KR_KOSDAQ', 'KOSDAQ')
with tab_us:
    render_watchlist_tab(us_tickers, 'US', '나스닥')
with tab_10ema_kospi:
    render_10ema_tab('KR_KOSPI', '10EMA 코스피')
with tab_10ema_kosdaq:
    render_10ema_tab('KR_KOSDAQ', '10EMA 코스닥')
with tab_10ema_us:
    render_10ema_tab('US', '10EMA 나스닥')
with tab_lb:
    render_leaderboard_tab()
```

- [ ] **Step 3: import 스모크 테스트**

Run: `python3 -c "import ast,sys; ast.parse(open('ui/leaderboard.py').read()); ast.parse(open('app.py').read()); print('구문 OK')"`
Expected: `구문 OK`

- [ ] **Step 4: 앱 실행해 탭 확인**

Run: `streamlit run app.py --server.headless true --server.port 8501`

브라우저에서 `👑 리더보드` 탭을 열어 확인:
- US 라디오 → 20종목 테이블, RS ≥ 80 초록 강조, 거래대금 `$3.8B` 형식
- KR 라디오 → "조건을 충족한 종목이 없습니다 — 주도주 부재 신호일 수 있습니다"
- 상단에 `📅 07-29 07:02 갱신 · 20종목`

확인 후 Ctrl+C로 종료.

- [ ] **Step 5: 커밋**

```bash
git add ui/leaderboard.py app.py
git commit -m "feat: 👑 리더보드 탭 추가 — US/KR 전환, 신선도 배지"
```

---

### Task 5: 교차 배지

**Files:**
- Modify: `ui/watchlist.py:492` (display_df 구성)
- Modify: `ui/watchlist_10ema.py:236` (display_df 구성)

**Interfaces:**
- Consumes: Task 1의 `data.leaderboard_store.get_tickers`
- Produces: 없음 (표시 변경만)

- [ ] **Step 1: watchlist.py에 배지 적용**

`ui/watchlist.py` import 블록에 추가:

```python
from data import leaderboard_store
```

같은 파일 상단(import 아래)에 매핑 상수 추가:

```python
# UI 시장 코드 → 리더보드 시장 코드
_LB_MARKET = {'KR_KOSPI': 'kr', 'KR_KOSDAQ': 'kr', 'US': 'us'}
```

492행 부근 `display_df = pd.DataFrame([{` 바로 위에 추가:

```python
    crown = leaderboard_store.get_tickers(_LB_MARKET.get(market, 'us'))
```

그리고 492행을 다음으로 교체:

```python
        '티커 | 종목명': ('👑 ' if r['Ticker'] in crown else '') + f"{r['Ticker']} | {r['종목명']}",
```

- [ ] **Step 2: watchlist_10ema.py에 배지 적용**

`ui/watchlist_10ema.py` import 블록에 추가:

```python
from data import leaderboard_store
```

상단에 매핑 상수 추가:

```python
_LB_MARKET = {'KR_KOSPI': 'kr', 'KR_KOSDAQ': 'kr', 'US': 'us'}
```

235행 `display_df = pd.DataFrame([{` 바로 위에 추가:

```python
    crown = leaderboard_store.get_tickers(_LB_MARKET.get(market, 'us'))
```

236행을 다음으로 교체:

```python
        '티커 | 종목명':  ('👑 ' if r['Ticker'] in crown else '') + f"{r['Ticker']} | {r['종목명']}",
```

- [ ] **Step 3: 구문 확인**

Run: `python3 -c "import ast; [ast.parse(open(f).read()) for f in ('ui/watchlist.py','ui/watchlist_10ema.py')]; print('구문 OK')"`
Expected: `구문 OK`

- [ ] **Step 4: 전체 테스트 회귀**

Run: `python3 -m pytest tests/ --ignore=tests/test_scoring.py -q`
Expected: PASS (기존 테스트 + 신규 테스트 전부)

- [ ] **Step 5: 앱에서 배지 확인**

Run: `streamlit run app.py --server.headless true --server.port 8501`

나스닥 탭에서 리더보드에 포함된 티커(예: `data/leaderboard/us.json`의 상위 종목)가 업로드 목록에 있으면 `👑 DELL | ` 형태로 보이는지 확인. 없으면 배지 없이 기존과 동일하게 표시되는지 확인.

확인 후 Ctrl+C로 종료.

- [ ] **Step 6: 커밋**

```bash
git add ui/watchlist.py ui/watchlist_10ema.py
git commit -m "feat: 와치리스트·10EMA 탭에 리더보드 👑 교차 배지"
```

---

### Task 6: 소스 파이프라인 호출 훅

**Files:**
- Modify: `~/Workspace/stockEdge/daily_review/orchestrator.py` (US, `run()` 끝)
- Modify: `~/Workspace/stockEdge/daily_review/orchestrator_kr.py` (KR, `run()` 끝)

> ⚠️ 이 태스크만 **다른 repo**를 수정한다. 커밋도 그쪽 repo에서 한다.

**Interfaces:**
- Consumes: Task 3의 `scripts/sync_leaderboard.py` CLI
- Produces: 없음

- [ ] **Step 1: US 오케스트레이터에 훅 추가**

`daily_review/orchestrator.py`의 `run()` 함수에서 `print("일간점검 완료")` 바로 앞에 추가:

```python
    # 대시보드 리더보드 동기화 — 실패해도 파이프라인은 성공 유지
    try:
        _sync_dashboard_leaderboard("us")
    except Exception as e:
        print(f"[orchestrator] 리더보드 동기화 실패(계속 진행): {e}")
```

같은 파일 맨 아래(`if __name__ == "__main__":` 앞)에 헬퍼 추가:

```python
def _sync_dashboard_leaderboard(market: str):
    """대시보드 repo로 리더보드 스냅샷을 푸시한다."""
    import subprocess

    script = Path.home() / "Workspace" / "stock-dashboard" / "scripts" / "sync_leaderboard.py"
    if not script.exists():
        print(f"[orchestrator] 동기화 스크립트 없음, 생략: {script}")
        return
    result = subprocess.run(
        ["python3", str(script), "--market", market],
        capture_output=True, text=True, timeout=120,
    )
    print(f"[orchestrator] 리더보드 동기화({market}) rc={result.returncode}")
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.returncode != 0 and result.stderr.strip():
        print(f"    stderr: {result.stderr.strip()[:300]}")
```

- [ ] **Step 2: KR 오케스트레이터에 훅 추가**

`daily_review/orchestrator_kr.py`의 `run()` 마지막 print 앞에 동일 호출 추가:

```python
    # 대시보드 리더보드 동기화 — 실패해도 파이프라인은 성공 유지
    try:
        _sync_dashboard_leaderboard("kr")
    except Exception as e:
        print(f"[orchestrator_kr] 리더보드 동기화 실패(계속 진행): {e}")
```

그리고 Step 1과 **동일한 `_sync_dashboard_leaderboard` 헬퍼**를 이 파일 맨 아래에도 추가한다 (두 오케스트레이터가 서로를 import하지 않으므로 각자 갖는다):

```python
def _sync_dashboard_leaderboard(market: str):
    """대시보드 repo로 리더보드 스냅샷을 푸시한다."""
    import subprocess

    script = Path.home() / "Workspace" / "stock-dashboard" / "scripts" / "sync_leaderboard.py"
    if not script.exists():
        print(f"[orchestrator_kr] 동기화 스크립트 없음, 생략: {script}")
        return
    result = subprocess.run(
        ["python3", str(script), "--market", market],
        capture_output=True, text=True, timeout=120,
    )
    print(f"[orchestrator_kr] 리더보드 동기화({market}) rc={result.returncode}")
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.returncode != 0 and result.stderr.strip():
        print(f"    stderr: {result.stderr.strip()[:300]}")
```

`Path`가 이미 import돼 있는지 확인하고(`from pathlib import Path`), 없으면 추가한다.

- [ ] **Step 3: 훅 동작 검증 (실제 푸시)**

Run: `cd ~/Workspace/stock-dashboard && python3 scripts/sync_leaderboard.py --market us`
Expected: `[us] 20종목 · 소스 갱신 ...` 출력, 종료 코드 0

GitHub에 반영됐는지 확인:
```bash
gh api repos/0908ohj-cmd/stock-dashboard/commits --jq '.[0].commit.message'
```
Expected: `data: 리더보드 US 20종목`

- [ ] **Step 4: cron 환경 토큰 검증**

⚠️ 스펙 §12의 남은 검증 항목. cron은 키체인이 잠겨 `gh auth token`이 실패할 수 있다.

Run: `env -i HOME="$HOME" PATH=/usr/bin:/bin:/opt/homebrew/bin python3 -c "
import subprocess
r = subprocess.run(['gh','auth','token'], capture_output=True, text=True)
print('rc=', r.returncode, 'has_token=', bool(r.stdout.strip()))
print('stderr:', r.stderr.strip()[:200])
"`

- 성공(`rc=0`)이면 추가 조치 없이 완료
- 실패하면 PAT를 발급해 stockEdge `.env`에 `DASHBOARD_GITHUB_TOKEN=ghp_...`로 등록하고, `run_daily.sh`가 `.env`를 읽어 환경변수로 넘기도록 한 줄 추가한다:

```bash
export DASHBOARD_GITHUB_TOKEN=$(grep '^DASHBOARD_GITHUB_TOKEN=' "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")
```

- [ ] **Step 5: stockEdge repo에 커밋**

```bash
cd ~/Workspace/stockEdge
git add daily_review/orchestrator.py daily_review/orchestrator_kr.py
git commit -m "feat: 일간점검 후 대시보드 리더보드 동기화 훅 추가"
```

---

## 최종 검증

- [ ] 전체 테스트: `python3 -m pytest tests/ --ignore=tests/test_scoring.py -q` → PASS
- [ ] 앱 실행: `streamlit run app.py` → 👑 리더보드 탭에 US 20종목 표시, KR 빈 상태 안내 표시
- [ ] 기존 탭에 👑 배지가 리더보드 종목에만 붙는지 확인
- [ ] `grep -ri "stockedge" ui/ data/ app.py` → **결과 없음** (소스 이름이 앱 계층에 노출되지 않을 것)
