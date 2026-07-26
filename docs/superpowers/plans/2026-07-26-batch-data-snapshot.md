# 배치 수집 + 저장소 우선 표시 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OHLCV를 장 마감 후 배치(GitHub Actions)와 업로드 직후 1회 수집으로 JSON 스냅샷에 영속화하고, 앱은 스냅샷을 읽기만 하게 만들어 반복 다운로드를 제거한다.

**Architecture:** 새 모듈 `data/store.py`가 시장별 JSON 스냅샷(`data/ohlcv/*.json`)의 읽기/쓰기/신선도 판정을 담당한다. `scripts/fetch_snapshot.py`가 GitHub Actions cron(KR 16:00 KST·US 07:00 KST)에서 스냅샷을 전체 교체 후 main에 커밋 → push가 Streamlit Cloud 재배포를 트리거한다. UI는 `fetch_daily`/`fetch_index_daily` 직접 호출을 store 로더로 교체한다.

**Tech Stack:** Python 3.11, pandas, yfinance/pykrx/FDR(기존 `fetch_daily` 재사용), GitHub Actions, Streamlit(ui 계층만)

**Spec:** `docs/superpowers/specs/2026-07-23-batch-data-snapshot-design.md`

## Global Constraints

- `data/store.py`·`scripts/`는 **Streamlit import 금지** (기존 `strategy/` 규칙과 동일 취지 — 단위 테스트 가능성)
- KR 시세는 반드시 기존 `data/fetcher.py:fetch_daily`의 pykrx→FDR 패치 체인을 경유 (store는 fetch_daily를 재사용하므로 자동 충족)
- 증분(append) 캐시 금지 — 배치·수동 재수집은 항상 **전체 교체** (액면분할 보정)
- 최종 와치리스트(rows) 저장 금지 — 저장 대상은 OHLCV만
- 종목 350달력일, 지수 400달력일 (`fetch_daily(days=350)` / `fetch_index_daily(days=400)`)
- `app.py` 상단 세션 최초 1회 `st.rerun()` 제거 금지
- 커밋 메시지 한국어, `feat:`/`fix:`/`docs:`/`test:` 접두사
- 테스트 실행: `python3 -m pytest tests/ --ignore=tests/test_scoring.py` (test_scoring.py는 스테일 — 반드시 ignore)
- 작업 브랜치: `feature/batch-snapshot`

## File Structure

- Create: `data/store.py` — 스냅샷 저장소 (변환·로드·미스 폴백·신선도·재수집)
- Create: `tests/test_store.py`
- Create: `scripts/__init__.py`, `scripts/fetch_snapshot.py` — 배치 CLI (티커 로드 → 수집 → 성공률 게이트 → 저장)
- Create: `tests/test_fetch_snapshot.py`
- Create: `requirements-batch.txt` — 배치 전용 최소 의존성
- Create: `.github/workflows/fetch-data.yml`
- Modify: `ui/watchlist.py` — 로더 교체 + 신선도 배지 + 재스캔 버튼
- Modify: `ui/index_panel.py` — 로더 교체 + 신선도 캡션
- Modify: `app.py` — 업로드 직후 1회 수집 + "데이터 재수집" 버튼 + 캡션 교체

---

### Task 1: `data/store.py` — 변환·로드·미스 폴백

**Files:**
- Create: `data/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `data.fetcher.fetch_daily(ticker, market, days)`, `data.fetcher.fetch_index_daily(name, days)`
- Produces (후속 태스크가 사용):
  - `store.OHLCV_DIR: pathlib.Path` (테스트에서 monkeypatch 대상)
  - `store.load_daily(ticker: str, market: str) -> pd.DataFrame` — DatetimeIndex + Open/High/Low/Close/Volume
  - `store.load_index(name: str) -> pd.DataFrame` — 동일 형태
  - `store.load_snapshot(market: str) -> dict` / `store.save_snapshot(market: str, snap: dict) -> None`
  - `store._df_to_records(df) -> dict` / `store._records_to_df(rec) -> pd.DataFrame`

- [ ] **Step 1: 브랜치 생성**

```bash
git checkout -b feature/batch-snapshot
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_store.py`

```python
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
```

- [ ] **Step 3: 실패 확인**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'data.store'` 또는 ImportError

- [ ] **Step 4: 구현** — `data/store.py`

```python
"""OHLCV 스냅샷 저장소.

배치(scripts/fetch_snapshot.py)·업로드 직후 수집분을 data/ohlcv/*.json에 영속화하고,
앱은 이 스냅샷을 읽기만 한다. 스냅샷에 없는 티커만 fetch_daily로 온디맨드 폴백.
Streamlit 무의존 — 캐시 래핑은 ui/ 계층에서 한다.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pandas as pd

from data.fetcher import fetch_daily, fetch_index_daily

OHLCV_DIR = pathlib.Path(__file__).parent / 'ohlcv'
KST = timezone(timedelta(hours=9))

STOCK_DAYS = 350   # ≈240거래일 — SMA200·52주 고점 요건
INDEX_DAYS = 400
INDEX_NAMES = ('KOSPI', 'KOSDAQ', 'NASDAQ')


def _snapshot_path(market: str) -> pathlib.Path:
    return OHLCV_DIR / f'{market}.json'


def _df_to_records(df: pd.DataFrame) -> dict:
    return {
        'dates':  [d.strftime('%Y-%m-%d') for d in df.index],
        'open':   [float(x) for x in df['Open']],
        'high':   [float(x) for x in df['High']],
        'low':    [float(x) for x in df['Low']],
        'close':  [float(x) for x in df['Close']],
        'volume': [float(x) for x in df['Volume']],
    }


def _records_to_df(rec: dict) -> pd.DataFrame:
    return pd.DataFrame({
        'Open':   rec['open'],
        'High':   rec['high'],
        'Low':    rec['low'],
        'Close':  rec['close'],
        'Volume': rec['volume'],
    }, index=pd.DatetimeIndex(pd.to_datetime(rec['dates'])))


def load_snapshot(market: str) -> dict:
    path = _snapshot_path(market)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_snapshot(market: str, snap: dict) -> None:
    OHLCV_DIR.mkdir(exist_ok=True)
    _snapshot_path(market).write_text(
        json.dumps(snap, ensure_ascii=False), encoding='utf-8')


def _merge_ticker(market: str, key: str, df: pd.DataFrame) -> None:
    """미스 폴백 수집분을 로컬 스냅샷에 병합. 메타(fetched_at)는 갱신하지 않는다 —
    티커 1개 폴백으로 시장 전체가 신선 판정되는 것을 방지."""
    snap = load_snapshot(market)
    snap.setdefault('market', market)
    snap.setdefault('data', {})
    snap['data'][key] = _df_to_records(df)
    save_snapshot(market, snap)


def load_daily(ticker: str, market: str) -> pd.DataFrame:
    """스냅샷 히트 시 즉시 반환(네트워크 0), 미스 시 fetch_daily 폴백 + 로컬 병합."""
    rec = load_snapshot(market).get('data', {}).get(ticker)
    if rec:
        return _records_to_df(rec)
    df = fetch_daily(ticker, market=market, days=STOCK_DAYS)
    if not df.empty:
        _merge_ticker(market, ticker, df)
    return df


def load_index(name: str) -> pd.DataFrame:
    rec = load_snapshot('indices').get('data', {}).get(name)
    if rec:
        return _records_to_df(rec)
    df = fetch_index_daily(name, days=INDEX_DAYS)
    if not df.empty:
        _merge_ticker('indices', name, df)
    return df
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: 4 passed

- [ ] **Step 6: 회귀 확인 후 커밋**

Run: `python3 -m pytest tests/ --ignore=tests/test_scoring.py -q`
Expected: 전체 통과

```bash
git add data/store.py tests/test_store.py
git commit -m "feat: OHLCV 스냅샷 저장소 모듈 추가 (로드·미스 폴백)"
```

---

### Task 2: `data/store.py` — 신선도 판정 + 전체 재수집

**Files:**
- Modify: `data/store.py` (Task 1 파일 끝에 추가)
- Test: `tests/test_store.py` (추가)

**Interfaces:**
- Produces (후속 태스크가 사용):
  - `store.get_freshness(market: str, now: datetime | None = None) -> dict` — `{'fetched_at': datetime|None, 'last_trading_date': str|None, 'is_stale': bool}`. `market`은 `'KR_KOSPI'|'KR_KOSDAQ'|'US'|'indices'`
  - `store.build_market_snapshot(market: str, tickers: list, fetch_fn=None) -> dict` — 수집만, 저장 안 함 (배치의 성공률 게이트용)
  - `store.refetch_market(market: str, tickers: list) -> dict` — 수집 + 전체 교체 저장
  - `store.refetch_indices() -> dict` — 지수 3종 수집 + 저장

- [ ] **Step 1: 실패하는 테스트 추가** — `tests/test_store.py` 끝에 추가

```python
def test_refetch_market_full_replace(tmp_store, monkeypatch):
    old = {'market': 'US', 'fetched_at': '2026-07-01T07:00:00+09:00',
           'data': {'OLD': store._df_to_records(_sample_df())}}
    (tmp_store / 'US.json').write_text(json.dumps(old), encoding='utf-8')
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
    snap = store.build_market_snapshot('US', ['AAPL', 'BAD'], fetch_fn=flaky)
    assert snap['ticker_count'] == 1
    assert snap['failed'] == ['BAD']
    assert not (tmp_store / 'US.json').exists()   # build는 저장하지 않는다


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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: 새 테스트들 FAIL — `AttributeError: ... 'refetch_market'`

- [ ] **Step 3: 구현** — `data/store.py` 끝에 추가

```python
# ── 신선도 판정 ──────────────────────────────────────────
# KST 기준 배치 예정: KR 월~금 16:00, US 화~토 07:00 (cron: 0 7 / 0 22 * * 1-5 UTC)
_BATCH_SCHEDULE = {
    'KR': {'hour': 16, 'weekdays': {0, 1, 2, 3, 4}},
    'US': {'hour': 7,  'weekdays': {1, 2, 3, 4, 5}},
}
_GRACE_HOURS = 6   # cron 지연·재배포 여유


def _last_deadline(schedule: dict, now: datetime) -> datetime:
    """유예를 반영해 '이미 돌았어야 하는' 가장 최근 배치 예정 시각을 반환."""
    cutoff = now - timedelta(hours=_GRACE_HOURS)
    d = cutoff.date()
    for _ in range(10):
        cand = datetime(d.year, d.month, d.day, schedule['hour'], tzinfo=KST)
        if cand.weekday() in schedule['weekdays'] and cand <= cutoff:
            return cand
        d -= timedelta(days=1)
    return cutoff - timedelta(days=10)


def get_freshness(market: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(KST)
    snap = load_snapshot(market)
    result = {
        'fetched_at': None,
        'last_trading_date': snap.get('last_trading_date'),
        'is_stale': False,
    }
    fetched_at_s = snap.get('fetched_at')
    if not fetched_at_s:
        return result   # 파일/메타 없음 → 경고하지 않음 (스펙 8절)
    fetched_at = datetime.fromisoformat(fetched_at_s)
    result['fetched_at'] = fetched_at
    if market == 'indices':   # 지수는 KR·US 두 배치 모두가 갱신 → 더 최근 기한 적용
        deadline = max(_last_deadline(_BATCH_SCHEDULE['KR'], now),
                       _last_deadline(_BATCH_SCHEDULE['US'], now))
    else:
        deadline = _last_deadline(
            _BATCH_SCHEDULE['KR' if market.startswith('KR') else 'US'], now)
    result['is_stale'] = fetched_at < deadline
    return result


# ── 전체 재수집 (배치·업로드 직후·수동 새로고침) ─────────
def build_market_snapshot(market: str, tickers: list, fetch_fn=None) -> dict:
    """전 종목 수집 → 스냅샷 dict 생성. 저장하지 않는다 (성공률 게이트는 호출부 책임)."""
    fetch = fetch_fn or fetch_daily
    data, failed, last_date = {}, [], None
    for t in tickers:
        try:
            df = fetch(t, market=market, days=STOCK_DAYS)
            if df.empty:
                failed.append(t)
                continue
            data[t] = _df_to_records(df)
            d = df.index[-1].strftime('%Y-%m-%d')
            last_date = max(last_date, d) if last_date else d
        except Exception:
            failed.append(t)
    return {
        'market': market,
        'fetched_at': datetime.now(KST).isoformat(timespec='seconds'),
        'last_trading_date': last_date,
        'ticker_count': len(data),
        'failed': failed,
        'data': data,
    }


def refetch_market(market: str, tickers: list) -> dict:
    """전체 수집 후 스냅샷 전체 교체 저장 (업로드 직후·수동 새로고침용)."""
    snap = build_market_snapshot(market, tickers)
    save_snapshot(market, snap)
    return snap


def refetch_indices() -> dict:
    data, last_date = {}, None
    for name in INDEX_NAMES:
        try:
            df = fetch_index_daily(name, days=INDEX_DAYS)
        except Exception:
            continue
        if df.empty:
            continue
        data[name] = _df_to_records(df)
        d = df.index[-1].strftime('%Y-%m-%d')
        last_date = max(last_date, d) if last_date else d
    snap = {
        'market': 'indices',
        'fetched_at': datetime.now(KST).isoformat(timespec='seconds'),
        'last_trading_date': last_date,
        'ticker_count': len(data),
        'failed': [],
        'data': data,
    }
    save_snapshot('indices', snap)
    return snap
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_store.py -v`
Expected: 11 passed

- [ ] **Step 5: 커밋**

```bash
git add data/store.py tests/test_store.py
git commit -m "feat: 스냅샷 신선도 판정·전체 재수집 추가"
```

---

### Task 3: 배치 스크립트 + 초기 스냅샷

**Files:**
- Create: `scripts/__init__.py` (빈 파일), `scripts/fetch_snapshot.py`
- Create: `requirements-batch.txt`
- Test: `tests/test_fetch_snapshot.py`

**Interfaces:**
- Consumes: `store.build_market_snapshot`, `store.save_snapshot`, `store.refetch_indices` (Task 2)
- Produces: CLI `python scripts/fetch_snapshot.py --markets kr|us|all` (Task 6 워크플로가 호출), `data/ohlcv/*.json` 초기 스냅샷

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_fetch_snapshot.py`

```python
from scripts import fetch_snapshot as fs


def test_run_market_gate_blocks_save(monkeypatch):
    """성공률 70% 미만이면 저장하지 않고 False 반환 (이전 스냅샷 유지)."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A', 'B', 'C', 'D'])
    bad = {'market': 'US', 'ticker_count': 1, 'failed': ['B', 'C', 'D'], 'data': {}}
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: bad)
    saved = []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(m))
    assert fs.run_market('US') is False
    assert saved == []


def test_run_market_gate_passes(monkeypatch):
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A', 'B', 'C', 'D'])
    good = {'market': 'US', 'ticker_count': 4, 'failed': [], 'data': {}}
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: good)
    saved = []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(m))
    assert fs.run_market('US') is True
    assert saved == ['US']


def test_run_market_no_ticker_file(monkeypatch):
    monkeypatch.setattr(fs, 'load_tickers', lambda m: [])
    assert fs.run_market('US') is True   # 티커 없음은 실패가 아니다
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/test_fetch_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts'`

- [ ] **Step 3: 구현** — `scripts/__init__.py`(빈 파일)와 `scripts/fetch_snapshot.py`

```python
#!/usr/bin/env python3
"""장 마감 후 OHLCV 스냅샷 배치 수집 (GitHub Actions cron에서 실행).

사용법: python scripts/fetch_snapshot.py --markets kr|us|all
휴장일에도 실행된다 — 데이터가 안 변해도 fetched_at 갱신이 신선도 판정의 근거.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import store

TICKER_FILES = {
    'KR_KOSPI':  'kospi.tickers',
    'KR_KOSDAQ': 'kosdaq.tickers',
    'US':        'us.tickers',
}
MARKET_GROUPS = {
    'kr':  ['KR_KOSPI', 'KR_KOSDAQ'],
    'us':  ['US'],
    'all': ['KR_KOSPI', 'KR_KOSDAQ', 'US'],
}
MIN_SUCCESS_RATE = 0.7


def load_tickers(market: str) -> list:
    path = (pathlib.Path(__file__).resolve().parent.parent
            / 'data' / 'saved' / TICKER_FILES[market])
    if not path.exists():
        return []
    return [t for t in path.read_text(encoding='utf-8').splitlines() if t.strip()]


def run_market(market: str) -> bool:
    """수집 → 성공률 게이트 통과 시에만 전체 교체 저장. 통과 여부 반환."""
    tickers = load_tickers(market)
    if not tickers:
        print(f'[{market}] 티커 파일 없음 — 건너뜀')
        return True
    snap = store.build_market_snapshot(market, tickers)
    rate = snap['ticker_count'] / len(tickers)
    if rate < MIN_SUCCESS_RATE:
        print(f'[{market}] 성공률 {rate:.0%} < {MIN_SUCCESS_RATE:.0%} — 스냅샷 미교체')
        return False
    store.save_snapshot(market, snap)
    print(f"[{market}] {snap['ticker_count']}/{len(tickers)}개 저장 "
          f"(실패 {len(snap['failed'])}: {snap['failed'][:5]})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--markets', choices=['kr', 'us', 'all'], default='all')
    args = ap.parse_args()

    results = [run_market(m) for m in MARKET_GROUPS[args.markets]]  # all() 단락 방지
    store.refetch_indices()
    print('[indices] 지수 3종 갱신')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
```

`requirements-batch.txt` (streamlit·plotly·aggrid 제외 최소셋 — 버전은 `requirements.txt`와 동일하게 고정):

```
pandas==2.3.3
numpy==2.4.6
yfinance==1.3.0
pykrx==1.2.8
finance-datareader==0.9.202
lxml
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 -m pytest tests/test_fetch_snapshot.py tests/test_store.py -v`
Expected: 14 passed

- [ ] **Step 5: 스모크 테스트 (실수집 — 네트워크 필요)**

Run: `python3 scripts/fetch_snapshot.py --markets us`
Expected: `[US] N/N개 저장` + `[indices] 지수 3종 갱신` 출력, `data/ohlcv/US.json`·`data/ohlcv/indices.json` 생성. `python3 -c "import json;d=json.load(open('data/ohlcv/US.json'));print(d['fetched_at'],d['ticker_count'])"`로 메타 확인

- [ ] **Step 6: 초기 스냅샷 전체 생성**

Run: `python3 scripts/fetch_snapshot.py --markets all` (KR 포함 — 수 분 소요)
Expected: 시장 3곳 + indices 저장, exit 0

- [ ] **Step 7: 커밋**

```bash
git add scripts/ requirements-batch.txt tests/test_fetch_snapshot.py data/ohlcv/
git commit -m "feat: 스냅샷 배치 수집 스크립트 + 초기 스냅샷"
```

---

### Task 4: UI 읽기 경로 교체 + 신선도 배지

**Files:**
- Modify: `ui/watchlist.py:7-10` (import), `:31-33` (`_fetch_index_cached`), `:107` (`fetch_daily` 호출), `:390-394` (재스캔 버튼), `:396` 이후 (신선도 배지)
- Modify: `ui/index_panel.py:25-28` (`_load_index`), `:31-33` (신선도 캡션)

**Interfaces:**
- Consumes: `store.load_daily`, `store.load_index`, `store.get_freshness`, `store.refetch_market`, `store.refetch_indices`
- Produces: 없음 (UI 말단)

- [ ] **Step 1: `ui/watchlist.py` 로더 교체**

import 블록에서 `fetch_daily, fetch_index_daily` 를 빼고 store를 추가:

```python
from data.fetcher import (
    get_stock_name,
    fetch_intraday_for_date, fetch_index_intraday_for_date,
)
from data import store
```

`_fetch_index_cached` 교체 (스냅샷은 400일 저장이므로 days 인자 불필요):

```python
@st.cache_data(ttl=1800)
def _fetch_index_cached(name: str) -> pd.DataFrame:
    return store.load_index(name)
```

`_build_rows` 내 수집 라인 교체 — `df = fetch_daily(ticker, market=market, days=350)` →

```python
            df = store.load_daily(ticker, market)
```

- [ ] **Step 2: 재스캔 버튼을 시장 단위 재수집으로 교체** — `render_watchlist_tab` 내 기존 `🔄 재스캔` 블록을:

```python
        st.divider()
        if st.button('🔄 재스캔', key=f'rescan_{market}', help='이 시장 시세를 지금 즉시 다시 수집합니다'):
            with st.spinner(f'{label} 재수집 중... ({len(tickers)}개 종목)'):
                store.refetch_market(market, tickers)
                store.refetch_indices()
            _build_rows.clear()
            _fetch_index_cached.clear()
            _get_market_status_cached.clear()
            st.rerun()
```

- [ ] **Step 3: 신선도 배지 추가** — `render_watchlist_tab`에서 `status = _get_market_status_cached(market)` 직전에:

```python
    fr = store.get_freshness(market)
    if fr['fetched_at']:
        st.caption(
            f"📅 {fr['last_trading_date']} 장마감 기준 · "
            f"수집 {fr['fetched_at'].strftime('%m-%d %H:%M')}"
        )
    if fr['is_stale']:
        st.warning('⚠️ 배치 수집이 실패한 것 같습니다 — 사이드바 [데이터 재수집]을 눌러 주세요.')
```

- [ ] **Step 4: `ui/index_panel.py` 교체**

```python
import streamlit as st
from data import store
from strategy.indicators import calc_adr
from strategy.phases import get_phase_label
```

`_load_index` 교체:

```python
@st.cache_data(ttl=300)
def _load_index(name: str):
    return store.load_index(name)
```

`render_index_panel` 첫 줄 `st.subheader('지수 현황')` 직후에 신선도 캡션:

```python
    fr = store.get_freshness('indices')
    if fr['fetched_at']:
        st.caption(
            f"📅 {fr['last_trading_date']} 장마감 기준 · "
            f"수집 {fr['fetched_at'].strftime('%m-%d %H:%M')}"
        )
```

- [ ] **Step 5: 회귀 + 수동 확인**

Run: `python3 -m pytest tests/ --ignore=tests/test_scoring.py -q`
Expected: 전체 통과

Run: `streamlit run app.py` → 브라우저에서: ① 탭 로딩이 수 초 내 완료 ② 각 탭·지수 패널에 `📅 ... 장마감 기준` 캡션 표시 ③ 재스캔 버튼 동작
Expected: 스냅샷 히트로 네트워크 수집 없이 렌더

- [ ] **Step 6: 커밋**

```bash
git add ui/watchlist.py ui/index_panel.py
git commit -m "feat: UI를 스냅샷 저장소 우선 읽기로 교체 + 신선도 배지"
```

---

### Task 5: `app.py` — 업로드 직후 1회 수집 + 데이터 재수집 버튼

**Files:**
- Modify: `app.py:6-8` (import), `:71-73` (사이드바 버튼·캡션), `:88-102` (업로드 처리), `:127` 이후 (재수집 핸들러)

**Interfaces:**
- Consumes: `store.refetch_market`, `store.refetch_indices`, `ui.watchlist._build_rows`, `ui.watchlist._get_market_status_cached`, `ui.watchlist._fetch_index_cached`, `ui.index_panel._load_index`

- [ ] **Step 1: import 수정**

```python
from data.fetcher import parse_tradingview_csv, parse_ticker_txt
from data import store
from ui.index_panel import render_index_panel, _load_index
from ui.watchlist import (
    render_watchlist_tab, _fetch_index_cached,
    _build_rows, _get_market_status_cached,
)
```

캐시 클리어 헬퍼를 `SAVED_PATHS` 정의 아래에 추가:

```python
def _clear_analysis_caches() -> None:
    _build_rows.clear()
    _fetch_index_cached.clear()
    _get_market_status_cached.clear()
    _load_index.clear()
```

- [ ] **Step 2: 사이드바 버튼·캡션 교체** — 기존 `🔄 새로고침` 버튼 + 15분 지연 캡션을:

```python
    if st.button('🔄 데이터 재수집', use_container_width=True,
                 help='전 시장 시세를 지금 즉시 다시 수집합니다 (비상용)'):
        st.session_state['force_refetch'] = True
    st.caption('📦 장 마감 후 배치 수집 데이터 (KR 16:00 · US 07:00 KST)')
```

- [ ] **Step 3: 업로드 직후 1회 수집** — 업로드 처리 `if uploaded:` 블록에서 `_github_save(...)` 다음에 추가. **주의: `st.file_uploader`는 파일이 올라간 동안 매 rerun마다 같은 값을 반환하므로 세션 시그니처 가드가 필수** (없으면 rerun마다 전량 재수집):

```python
            sig = f'{fname}:{len(raw)}'
            if st.session_state.get(f'uploaded_sig_{key}') != sig:
                st.session_state[f'uploaded_sig_{key}'] = sig
                with st.sidebar:
                    with st.spinner(f'{name} 시세 수집 중... ({len(tickers_parsed)}개 종목)'):
                        store.refetch_market(key, tickers_parsed)
                _clear_analysis_caches()
```

- [ ] **Step 4: 재수집 핸들러 추가** — 백업 다운로드 블록과 `render_index_panel()` 사이에:

```python
# ── 수동 데이터 재수집 (비상용) ───────────────────────────
if st.session_state.pop('force_refetch', False):
    for key, tickers in [('KR_KOSPI', kr_kospi), ('KR_KOSDAQ', kr_kosdaq), ('US', us_tickers)]:
        if tickers:
            with st.spinner(f'{key} 재수집 중... ({len(tickers)}개 종목)'):
                store.refetch_market(key, tickers)
    with st.spinner('지수 재수집 중...'):
        store.refetch_indices()
    _clear_analysis_caches()
    st.rerun()
```

- [ ] **Step 5: 수동 확인 + 회귀**

Run: `streamlit run app.py` → ① 새 CSV 업로드 시 사이드바 스피너로 1회 수집 후 즉시 분석 표시, 같은 파일 유지 상태로 rerun해도 재수집 안 함 ② [데이터 재수집] 버튼으로 전 시장 재수집 ③ 기존 세션 최초 1회 `st.rerun()` 동작 유지
Run: `python3 -m pytest tests/ --ignore=tests/test_scoring.py -q`
Expected: 전체 통과

- [ ] **Step 6: 커밋**

```bash
git add app.py
git commit -m "feat: 업로드 직후 1회 수집 + 수동 데이터 재수집 버튼"
```

---

### Task 6: GitHub Actions 워크플로

**Files:**
- Create: `.github/workflows/fetch-data.yml`

**Interfaces:**
- Consumes: `scripts/fetch_snapshot.py` CLI (Task 3), `requirements-batch.txt`

- [ ] **Step 1: 워크플로 작성** — `.github/workflows/fetch-data.yml`

```yaml
name: 스냅샷 수집

on:
  schedule:
    - cron: '0 7 * * 1-5'    # 16:00 KST 월~금 → KR
    - cron: '0 22 * * 1-5'   # 07:00 KST 화~토 → US
  workflow_dispatch:
    inputs:
      markets:
        description: '수집 대상'
        type: choice
        options: [all, kr, us]
        default: all

permissions:
  contents: write

concurrency:
  group: fetch-data
  cancel-in-progress: false

jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - run: pip install -r requirements-batch.txt

      - name: 대상 시장 결정
        id: target
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            echo "markets=${{ inputs.markets }}" >> "$GITHUB_OUTPUT"
          elif [ "${{ github.event.schedule }}" = "0 7 * * 1-5" ]; then
            echo "markets=kr" >> "$GITHUB_OUTPUT"
          else
            echo "markets=us" >> "$GITHUB_OUTPUT"
          fi

      - name: 스냅샷 수집
        run: python scripts/fetch_snapshot.py --markets ${{ steps.target.outputs.markets }}

      - name: 커밋 & push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/ohlcv
          git diff --cached --quiet && { echo "변경 없음"; exit 0; }
          git commit -m "data: ${{ steps.target.outputs.markets }} 스냅샷 $(date -u -d '+9 hours' +%F)"
          git pull --rebase origin main
          git push origin main
```

- [ ] **Step 2: YAML 문법 확인**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/fetch-data.yml')); print('OK')"`
Expected: `OK` (pyyaml 없으면 `pip install pyyaml` 후 실행)

- [ ] **Step 3: 커밋**

```bash
git add .github/workflows/fetch-data.yml
git commit -m "feat: 스냅샷 수집 GitHub Actions 워크플로 추가"
```

- [ ] **Step 4: 배포 후 검증 (main 머지·push 이후 — 사용자 허가 필요 단계)**

1. GitHub → Actions → "스냅샷 수집" → Run workflow (`markets: all`)로 1회 수동 실행
2. `data: all 스냅샷 ...` 커밋 생성 + Streamlit Cloud 재배포 확인
3. 앱 접속 → 로딩 없이 즉시 표시 + `📅 ... 장마감 기준` 캡션 확인
4. 다음 거래일 16:00 KST / 07:00 KST 자동 실행 확인

---

## 스펙 커버리지 체크

| 스펙 절 | 태스크 |
|---------|--------|
| 5. 데이터 저장소 (JSON 스키마) | Task 1·2 |
| 6. 배치 스크립트·워크플로 | Task 3·6 |
| 7. store.py 로더·미스 폴백·업로드 흐름 | Task 1·2·4·5 |
| 8. 신선도 표시 + 실패 감지 | Task 2 (판정)·4 (배지) |
| 9. 수동 새로고침 | Task 4 (재스캔)·5 (사이드바) |
| 10. 에러 처리 | Task 2 (build 실패 기록)·3 (게이트) |
| 11. 테스트 | Task 1·2·3 |
| 12. 롤아웃 | Task 3 Step 6 (초기 스냅샷)·Task 6 Step 4 (검증) |
