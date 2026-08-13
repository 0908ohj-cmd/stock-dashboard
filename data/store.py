"""OHLCV 스냅샷 저장소.

배치(scripts/fetch_snapshot.py)·업로드 직후 수집분을 data/ohlcv/*.json에 영속화하고,
앱은 이 스냅샷을 읽기만 한다. 스냅샷에 없는 티커만 fetch_daily로 온디맨드 폴백.
Streamlit 무의존 — 캐시 래핑은 ui/ 계층에서 한다.
"""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pandas as pd

from data.fetcher import fetch_daily, fetch_daily_bulk_us, fetch_index_daily

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


def _merge_history(new_df: pd.DataFrame, old_rec: dict | None, window_days: int,
                   label: str | None = None) -> pd.DataFrame:
    """새 수집분에 빠진 과거 거래일을 기존 레코드에서 보존 (일시 결손 방어).

    2026-08-12 배치에서 yfinance가 KOSPI 3거래일(찐반등봉 포함)을 빠뜨린 응답을 줬고,
    통째 교체 저장이 멀쩡하던 과거 데이터를 소실시켰다 → 날짜 기준 병합으로 방어.
    - 겹치는 날짜는 새 값 우선
    - 새 수집분 마지막 날짜 이후의 기존 행은 되살리지 않는다 (당일 패치 잔재 영구화 방지)
    - 마지막 날짜 기준 window_days(캘린더) 롤링 윈도우로 트림 (무한 증식 방지)
    - label 지정 시 보존이 실제 발동하면 로그 출력 (배치 로그에서 수집 이상 가시화).
      단, 윈도우 경계에서 매일 하루씩 밀려나는 선두 rolloff(1~2일)는 로그하지 않는다 —
      중간 결손 또는 선두 3일 이상 결손만 이상 신호로 본다.
    """
    new_min, new_max = new_df.index.min(), new_df.index.max()
    if old_rec:
        old_df = _records_to_df(old_rec)
        old_df = old_df[old_df.index <= new_max]
        preserved = old_df.index.difference(new_df.index)
        new_df = new_df.combine_first(old_df)
    else:
        preserved = pd.DatetimeIndex([])
    cutoff = new_max - pd.Timedelta(days=window_days)
    preserved = preserved[preserved >= cutoff]
    interior = preserved[preserved > new_min]
    leading  = preserved[preserved < new_min]
    if label and (len(interior) or len(leading) >= 3):
        dates = [d.strftime('%Y-%m-%d') for d in preserved]
        shown = ', '.join(dates[:5]) + (f' 외 {len(dates) - 5}일' if len(dates) > 5 else '')
        print(f'[{label}] 새 수집분에서 {len(dates)}거래일 결손 → 기존 값 보존: {shown}')
    return new_df[new_df.index >= cutoff]


# market → (mtime_ns, snap) — 티커별 반복 로드 시 수 MB JSON 재파싱(O(N²)) 방지.
# 파일 mtime이 바뀌면(배치 재배포·재수집) 자동 무효화. Streamlit rerun 간에도 유지.
_snap_cache: dict = {}


def load_snapshot(market: str) -> dict:
    path = _snapshot_path(market)
    if not path.exists():
        _snap_cache.pop(market, None)
        return {}
    mtime = path.stat().st_mtime_ns
    hit = _snap_cache.get(market)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        snap = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    _snap_cache[market] = (mtime, snap)
    return snap


def save_snapshot(market: str, snap: dict) -> None:
    OHLCV_DIR.mkdir(exist_ok=True)
    path = _snapshot_path(market)
    path.write_text(json.dumps(snap, ensure_ascii=False), encoding='utf-8')
    _snap_cache[market] = (path.stat().st_mtime_ns, snap)


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


# ── 신선도 판정 ──────────────────────────────────────────
# KST 기준 배치 예정: KR 월~금 15:45, US 화~토 07:00 (cron: 45 6 / 0 22 * * 1-5 UTC)
# 기한(deadline)은 정각(15:00) 기준 보수 판정 — 45분 차이는 6h 유예가 흡수한다
_BATCH_SCHEDULE = {
    'KR': {'hour': 15, 'weekdays': {0, 1, 2, 3, 4}},
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
def build_market_snapshot(market: str, tickers: list, fetch_fn=None,
                          throttle_sec: float = 0.25) -> dict:
    """전 종목 수집 → 스냅샷 dict 생성. 저장하지 않는다 (성공률 게이트는 호출부 책임).

    throttle_sec: 호출 간 대기 — 수백 종목 연속 호출 시 Yahoo 레이트리밋 방지.
    실패 티커는 잠시 대기 후 1회 재시도 (일시적 레이트리밋 회복).
    """
    import time

    tickers = list(dict.fromkeys(tickers))   # 순서 보존 dedupe — 파일 중복 라인 방어
    fetch = fetch_fn or fetch_daily
    data, last_date = {}, None
    old_data = load_snapshot(market).get('data', {})   # 날짜 병합용 (일시 결손 방어)

    def _try(t) -> bool:
        nonlocal last_date
        try:
            df = fetch(t, market=market, days=STOCK_DAYS)
            if df.empty:
                return False
            data[t] = _df_to_records(
                _merge_history(df, old_data.get(t), STOCK_DAYS, label=f'{market}:{t}'))
            d = df.index[-1].strftime('%Y-%m-%d')
            last_date = max(last_date, d) if last_date else d
            return True
        except Exception:
            return False

    failed = []
    if fetch_fn is None and market == 'US':
        # US 벌크 경로 — 요청 수 390→8회로 축소 (프로세스당 ~245요청 세션 만료 회피)
        bulk = fetch_daily_bulk_us(tickers, days=STOCK_DAYS)
        misses = []
        for t in tickers:
            df = bulk.get(t)
            if df is not None and not df.empty:
                try:
                    data[t] = _df_to_records(
                        _merge_history(df, old_data.get(t), STOCK_DAYS, label=f'{market}:{t}'))
                    d = df.index[-1].strftime('%Y-%m-%d')
                    last_date = max(last_date, d) if last_date else d
                except Exception:
                    misses.append(t)
            else:
                misses.append(t)
        for t in misses:              # 벌크 미스만 개별 폴백 1회
            if not _try(t):
                failed.append(t)
            if throttle_sec:
                time.sleep(throttle_sec)
    else:
        for t in tickers:
            if not _try(t):
                failed.append(t)
            if throttle_sec:
                time.sleep(throttle_sec)

        if failed:                    # 재시도 패스 — 레이트리밋 완화 대기 후
            if throttle_sec:
                time.sleep(throttle_sec * 12)
            retry, failed = failed, []
            for t in retry:
                if not _try(t):
                    failed.append(t)
                if throttle_sec:
                    time.sleep(max(throttle_sec, 0.5))

    return {
        'market': market,
        'fetched_at': datetime.now(KST).isoformat(timespec='seconds'),
        'last_trading_date': last_date,
        'ticker_count': len(data),
        'failed': failed,
        'data': data,
    }


def refetch_market(market: str, tickers: list, throttle_sec: float = 0.25) -> dict:
    """전체 수집 후 스냅샷 전체 교체 저장 (업로드 직후·수동 새로고침용).

    전량 실패(네트워크 장애 등) 시에는 저장하지 않는다 — 기존 스냅샷 보존.
    """
    snap = build_market_snapshot(market, tickers, throttle_sec=throttle_sec)
    if snap['ticker_count'] == 0 and tickers:
        return snap
    save_snapshot(market, snap)
    return snap


_MARKET_INDEX_MAP = {
    'kr':  ('KOSPI', 'KOSDAQ'),
    'us':  ('NASDAQ',),
    'all': INDEX_NAMES,
}


def refetch_indices(markets: str = 'all') -> dict:
    to_update = _MARKET_INDEX_MAP.get(markets, INDEX_NAMES)

    # 기존 데이터 보존 — 업데이트 대상 외 지수(예: KR 배치 시 NASDAQ)는 덮어쓰지 않음
    existing   = load_snapshot('indices')
    merged     = dict(existing.get('data', {}))

    for name in to_update:
        try:
            df = fetch_index_daily(name, days=INDEX_DAYS)
        except Exception:
            continue
        if df.empty:
            continue
        merged[name] = _df_to_records(
            _merge_history(df, merged.get(name), INDEX_DAYS, label=name))

    last_date = None
    for rec in merged.values():
        d = rec['dates'][-1] if rec.get('dates') else None
        if d:
            last_date = max(last_date, d) if last_date else d

    snap = {
        'market': 'indices',
        'fetched_at': datetime.now(KST).isoformat(timespec='seconds'),
        'last_trading_date': last_date,
        'ticker_count': len(merged),
        'failed': [],
        'data': merged,
    }
    if not merged:                    # 전량 실패 — 기존 indices.json 보존
        return snap
    save_snapshot('indices', snap)
    return snap
