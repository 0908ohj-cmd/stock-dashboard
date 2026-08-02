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
