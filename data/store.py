"""OHLCV 스냅샷 저장소.

배치(scripts/fetch_snapshot.py)·업로드 직후 수집분을 data/ohlcv/*.json에 영속화하고,
앱은 이 스냅샷을 읽기만 한다. 스냅샷에 없는 티커만 fetch_daily로 온디맨드 폴백.
Streamlit 무의존 — 캐시 래핑은 ui/ 계층에서 한다.
"""
import json
import pathlib
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

from data._atomic import atomic_write_text
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


_LOG_RECENT_DAYS = 30   # 이보다 오래된 결손은 로그하지 않는다 — 소스가 영영 안 주는 날짜가
                        # 매 배치마다 재로그돼 진짜 이상 신호를 묻는 것을 막는다
_TAIL_GRACE_DAYS = 7    # 꼬리 절단 보존 한도. 이보다 미래의 기존 행은 오염으로 보고 버린다 —
                        # 남겨두면 롤링 윈도우 기준일을 밀어 실데이터를 깎는다
_RESCALE_TOL = 0.005    # 겹치는 날짜 종가 괴리 허용치 (분할·배당 재조정 감지)
_COLLAPSE_RATIO = 0.5   # 수집 행수가 기존의 이 비율 미만이면 '범위 축소'로 경보
_OHLCV_COLS = ('Open', 'High', 'Low', 'Close', 'Volume')


def _tz_naive(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """tz-aware 인덱스를 naive로 통일 — 섞이면 모든 날짜 비교가 TypeError로 죽는다."""
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize(None) if idx.tz is not None else idx


def _price_scale_changed(old_df: pd.DataFrame, new_df: pd.DataFrame) -> bool:
    """소스가 과거 시세를 재조정했는지(분할·배당) 겹치는 날짜 종가비의 중앙값으로 판정.

    auto_adjust=True라 배당·분할 때마다 전 구간이 재조정된다. 재조정 후 옛 스케일 행을
    보존하면 +102%/-50% 유령봉이 되어 ADR·EMA21·찐반등 판정을 통째로 오염시킨다.
    """
    common = old_df.index.intersection(new_df.index)
    if len(common) == 0:
        return False                      # 비교 불가 — 보존 유지 (판단 보류)
    o = old_df.loc[common, 'Close'].astype(float)
    n = new_df.loc[common, 'Close'].astype(float)
    valid = (o > 0) & n.notna()
    if not valid.any():
        return False
    return bool(abs(float((n[valid] / o[valid]).median()) - 1) > _RESCALE_TOL)


def _merge_history(new_df: pd.DataFrame, old_rec: dict | None, window_days: int,
                   label: str | None = None) -> pd.DataFrame:
    """새 수집분에 빠진 거래일을 기존 레코드에서 보존 (일시 결손 방어).

    2026-08-12 배치에서 yfinance가 KOSPI 3거래일(찐반등봉 포함)을 빠뜨린 응답을 줬고,
    통째 교체 저장이 멀쩡하던 과거 데이터를 소실시켰다 → 날짜 기준 병합으로 방어.
    - 겹치는 날짜는 새 수집분 행을 통째로 채택. 셀 단위 병합(combine_first)은 새 수집분의
      NaN을 옛값으로 메워 Close > High 같은 모순 행을 만들므로 쓰지 않는다
    - 꼬리가 잘린 응답도 중간 결손과 동일하게 취급해 기존 최신 거래일을 보존한다
    - 마지막 날짜 기준 window_days(캘린더) 롤링 윈도우로 트림 (무한 증식 방지)
    - label 지정 시 이상 결손만 로그. 수집 윈도우가 앞으로 밀리며 빠지는 선두
      rolloff는 정상이므로 제외한다 (휴일 클러스터 뒤엔 여러 날이 한꺼번에 밀린다)

    컬럼이 빠진 수집분은 병합으로 메우지 않고 KeyError를 낸다 — 옛 컬럼으로 채우면
    NaN 거래량이 스냅샷에 굳고, 벌크 경로가 개별 폴백으로 강등되지 못한다.
    """
    missing_cols = [c for c in _OHLCV_COLS if c not in new_df.columns]
    if missing_cols:
        raise KeyError(f'수집분 컬럼 누락: {missing_cols}')

    new_df = new_df.copy()
    new_df.index = _tz_naive(new_df.index)
    new_df = new_df[~new_df.index.duplicated(keep='last')]
    if new_df.empty:
        return new_df

    fetch_min, fetch_max = new_df.index.min(), new_df.index.max()
    fetch_rows = len(new_df)
    preserved  = new_df.index[:0]       # 같은 dtype의 빈 인덱스 (비교 시 dtype 충돌 방지)
    old_rows   = 0
    if old_rec:
        old_df = _records_to_df(old_rec)
        old_df.index = _tz_naive(old_df.index)
        old_df = old_df[~old_df.index.duplicated(keep='last')]
        if _price_scale_changed(old_df, new_df):
            if label:
                print(f'[{label}] 시세 재조정 감지 — 병합 생략(수집분으로 전체 교체)')
            old_df = old_df.iloc[:0]
        # 꼬리 유예를 넘는 미래 행은 오염 — 남기면 아래 cutoff 기준일을 밀어 실데이터를 깎는다
        old_df    = old_df[old_df.index <= fetch_max + pd.Timedelta(days=_TAIL_GRACE_DAYS)]
        old_rows  = len(old_df)
        preserved = old_df.index.difference(new_df.index)
        new_df    = pd.concat([new_df, old_df.loc[preserved]]).sort_index()

    # 기준일은 수집분의 마지막 날짜 — 보존 행이 기준일을 좌우하면 오염에 취약해진다
    cutoff = max(fetch_max, new_df.index.max()) - pd.Timedelta(days=window_days)
    merged = new_df[new_df.index >= cutoff]

    if label:
        # 수집 범위가 통째로 쪼그라든 경우 — 데이터는 보존되지만 소스 이상이므로 경보.
        # 선두 rolloff 제외 규칙에 걸려 아래 결손 로그로는 안 잡힌다
        if old_rows and fetch_rows < old_rows * _COLLAPSE_RATIO:
            print(f'[{label}] 수집 범위 축소 — 기존 {old_rows}행 → 수집 {fetch_rows}행')
        gaps = preserved[(preserved > fetch_min) & (preserved >= cutoff)]
        gaps = gaps[gaps >= merged.index.max() - pd.Timedelta(days=_LOG_RECENT_DAYS)]
        if len(gaps):
            dates = [d.strftime('%Y-%m-%d') for d in gaps]
            shown = ', '.join(dates[:5]) + (f' 외 {len(dates) - 5}일' if len(dates) > 5 else '')
            print(f'[{label}] 새 수집분에서 {len(dates)}거래일 결손 → 기존 값 보존: {shown}')
    return merged


def _safe_merge(new_df: pd.DataFrame, old_rec: dict | None, window_days: int,
                label: str) -> pd.DataFrame:
    """병합 실패가 수집분 자체를 잃게 만들면 안 된다 — 손실 방어 로직이 손실의 원인이
    되는 것을 막는 최후 방어선. 예외 시 새 수집분을 그대로 쓴다."""
    try:
        return _merge_history(new_df, old_rec, window_days, label=label)
    except Exception as e:
        print(f'[{label}] 병합 실패({type(e).__name__}: {e}) — 새 수집분만 저장')
        # 폴백 경로에서도 중복 날짜는 걸러야 한다 — 스냅샷에 굳으면 .loc이 Series를
        # 반환해 전략 계층이 크래시한다
        new_df = new_df.copy()
        new_df.index = _tz_naive(new_df.index)
        return new_df[~new_df.index.duplicated(keep='last')]


# market → (mtime_ns, snap) — 티커별 반복 로드 시 수 MB JSON 재파싱(O(N²)) 방지.
# 파일 mtime이 바뀌면(배치 재배포·재수집) 자동 무효화. Streamlit rerun 간에도 유지.
# 캐시된 dict는 여러 스레드·세션이 같이 읽는다 — 절대 제자리에서 고치지 말고 복사본을 저장할 것.
_snap_cache: dict = {}

# 스냅샷 읽기→수정→쓰기 직렬화 (#26). 10EMA 탭은 ThreadPoolExecutor(10)로 load_daily를 돌려
# 미스 병합이 동시에 일어난다. RLock — _merge_ticker가 잡은 채로 save_snapshot을 부른다.
_snap_lock = threading.RLock()


class _UnreadableSnapshot(Exception):
    """스냅샷 파일이 있는데 파싱할 수 없다. 쓰기 경로는 이 파일을 덮어쓰면 안 된다."""


def _read_snapshot(market: str) -> dict:
    """스냅샷 읽기. 파일이 없으면 {}, 있는데 파싱이 안 되면 _UnreadableSnapshot."""
    path = _snapshot_path(market)
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        _snap_cache.pop(market, None)
        return {}
    hit = _snap_cache.get(market)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        snap = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise _UnreadableSnapshot(f'{path.name}: {type(e).__name__}: {e}') from e
    if not isinstance(snap, dict):
        raise _UnreadableSnapshot(f'{path.name}: 최상위가 dict가 아님({type(snap).__name__})')
    _snap_cache[market] = (mtime, snap)
    return snap


def load_snapshot(market: str) -> dict:
    """읽기 전용 경로 — 파싱 실패는 빈 스냅샷으로 본다 (화면은 온디맨드 폴백으로 버틴다).
    쓰기 경로는 이 함수 대신 _read_snapshot을 써서 파싱 실패 시 저장을 건너뛴다."""
    try:
        return _read_snapshot(market)
    except _UnreadableSnapshot:
        return {}


def save_snapshot(market: str, snap: dict) -> None:
    """원자적 저장 — 쓰는 도중에 읽어도 이전 파일이 온전히 보인다 (#26)."""
    with _snap_lock:
        OHLCV_DIR.mkdir(exist_ok=True)
        path = _snapshot_path(market)
        atomic_write_text(path, json.dumps(snap, ensure_ascii=False))
        _snap_cache[market] = (path.stat().st_mtime_ns, snap)


def _merge_ticker(market: str, key: str, df: pd.DataFrame) -> None:
    """미스 폴백 수집분을 로컬 스냅샷에 병합. 메타(fetched_at)는 갱신하지 않는다 —
    티커 1개 폴백으로 시장 전체가 신선 판정되는 것을 방지.

    파일을 파싱할 수 없으면 병합을 건너뛴다. 예전엔 파싱 실패를 빈 스냅샷으로 보고 종목
    1개만 넣어 저장해 시장 전체가 사라졌다 (#26)."""
    rec = _df_to_records(df)
    with _snap_lock:
        try:
            snap = _read_snapshot(market)
        except _UnreadableSnapshot as e:
            print(f'[{market}] 스냅샷을 읽을 수 없어 {key} 병합 생략 — 파일은 그대로 둔다 ({e})')
            return
        save_snapshot(market, {**snap, 'market': snap.get('market', market),
                               'data': {**snap.get('data', {}), key: rec}})


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
# KST 기준 배치 예정: KR 월~금 17:30, US 화~토 08:10 (맥 launchd)
# 기한(deadline)은 정각 기준 보수 판정 — 분 단위 차이는 6h 유예가 흡수한다
_BATCH_SCHEDULE = {
    'KR': {'hour': 17, 'weekdays': {0, 1, 2, 3, 4}},
    'US': {'hour': 8,  'weekdays': {1, 2, 3, 4, 5}},
}
_GRACE_HOURS = 6   # 배치 지연·재배포 여유
# 연속 거래일 사이 최대 달력 간격. 실측(2025-08~2026-09) KR 최대가 8일이었다 —
# 2025 추석에 개천절(10/3)·한글날(10/9)이 붙어 10/2(목) → 10/10(금). 2026 설이 6일,
# US 최대가 4일. 이보다 벌어지면 휴장이 아니라 소스가 낡은 것으로 본다.
# 너무 좁히면 연휴마다 오경보가 나고, 그 오경보에 질려 경고를 꺼버린 게
# 2026-09-01 fdc5c8f였다. 흔한 실패(배치 미실행)는 stale_by_batch가 몇 시간
# 안에 잡으므로, 이 값이 커져 늦어지는 건 "배치는 돌았는데 소스가 동결된"
# 드문 경우의 탐지뿐이다.
_MAX_TRADING_GAP_DAYS = 8
_SNAPSHOT_INDEX = {'KR_KOSPI': 'KOSPI', 'KR_KOSDAQ': 'KOSDAQ', 'US': 'NASDAQ'}   # 같은 배치가 받는 시장 지수


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

    # 신선하려면 두 조건을 모두 만족해야 한다 (스펙 §6.6):
    #   ① 배치가 기한 내에 돌았다        → stale_by_batch
    #   ② 데이터의 거래일이 오래되지 않았다 → stale_by_data (_MAX_TRADING_GAP_DAYS)
    # 2026-09-01 fdc5c8f는 ②를 "4일 이내면 무조건 신선"이라는 단축 경로로 써서
    # ①을 건너뛰게 만들었고, 배치가 1~3일 실패하는 구간이 정확히 그 창에 들어가
    # 경고가 통째로 죽었다. 지금은 ②가 ①을 대신하지 않고 **덧붙는다**.
    # 그 false alarm의 원인이던 GitHub cron 지연은 배치를 로컬 launchd로 옮기며
    # 사라졌고, 남은 분 단위 편차는 _GRACE_HOURS가 흡수한다.
    if market == 'indices':   # 지수는 KR·US 두 배치 모두가 갱신 → 더 최근 기한 적용
        deadline = max(_last_deadline(_BATCH_SCHEDULE['KR'], now),
                       _last_deadline(_BATCH_SCHEDULE['US'], now))
    else:
        deadline = _last_deadline(
            _BATCH_SCHEDULE['KR' if market.startswith('KR') else 'US'], now)
    stale_by_batch = fetched_at < deadline
    # 배치가 제때 돌았어도 소스가 옛 거래일 데이터를 주면 낡은 것이다 (스펙 §6.6)
    stale_by_data = False
    last_td_s = snap.get('last_trading_date')
    if last_td_s:
        try:
            last_td = datetime.strptime(last_td_s, '%Y-%m-%d').date()
            stale_by_data = (now.date() - last_td).days > _MAX_TRADING_GAP_DAYS
        except ValueError:
            pass
    # 배치가 돌았어도 종목이 같은 시장 지수보다 하루 늦으면 낡은 것이다 (2026-11-10 US: 지수 11/09·종목 11/06).
    # 휴장일엔 지수에도 그날이 없어 오경보가 나지 않는다.
    stale_by_index = False
    idx_name = _SNAPSHOT_INDEX.get(market)
    if idx_name and last_td_s:
        rec = load_snapshot('indices').get('data', {}).get(idx_name) or {}
        idx_last = (rec.get('dates') or [None])[-1]
        if idx_last and last_td_s < idx_last:
            stale_by_index = True
            result['behind_index'] = idx_last
    result['is_stale'] = stale_by_batch or stale_by_data or stale_by_index
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
            merged = _safe_merge(df, old_data.get(t), STOCK_DAYS, f'{market}:{t}')
            data[t] = _df_to_records(merged)
            # 병합 후 프레임 기준 — 꼬리 절단을 보존으로 살린 날짜가 반영돼야 한다
            d = merged.index[-1].strftime('%Y-%m-%d')
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
                    merged = _safe_merge(df, old_data.get(t), STOCK_DAYS, f'{market}:{t}')
                    data[t] = _df_to_records(merged)
                    d = merged.index[-1].strftime('%Y-%m-%d')
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

    fresh_count = len(data)   # 성공률 게이트 분모용 — 아래 보존분은 포함하지 않는다

    # 수집 실패 티커의 기존 히스토리를 통째로 버리지 않는다. 날짜 결손보다 큰 손실이고
    # (350일 전량), 이게 없으면 앱이 종목마다 실시간 폴백을 돌아 첫 로딩이 느려진다.
    for t in failed:
        if old_data.get(t):
            data[t] = old_data[t]

    return {
        'market': market,
        'fetched_at': datetime.now(KST).isoformat(timespec='seconds'),
        'last_trading_date': last_date,
        'ticker_count': fresh_count,
        'failed': failed,
        'data': data,
    }


def refetch_market(market: str, tickers: list, throttle_sec: float = 0.25) -> dict:
    """앱 안 재수집(업로드 직후·🔄 데이터 재수집·탭별 🔄 재스캔) — 받은 종목만 갈아끼우는 부분 갱신.

    배치 스냅샷은 여러 티커 파일의 합집합(fetch_snapshot.TICKER_FILES)인데, 호출부는 자기
    목록만 넘긴다. 예전처럼 그 목록으로 통째 교체하면 다른 탭의 유니버스(10EMA·주도섹터
    210종목·QQQ·IWM)가 지워졌다 (#31). 대상 밖 종목은 저장 직전 최신본에서 그대로 둔다.

    시장 메타(fetched_at·last_trading_date·ticker_count·failed)는 '전 종목을 언제 받았나'의
    기록이라 대상이 기존 종목을 전부 덮을 때만 새로 쓴다. 일부만 받았으면 메타는 그대로 두고
    partial_refetched_at에 시각만 남긴다 — fetched_at을 올리면
      ① get_freshness가 다시 받지 않은 종목까지 신선하다고 판정하고
      ② repo_sync._local_wins가 이 파일을 그보다 이른 시각에 찍힌 원격 배치(전 종목 최신)보다
         우선해, 남은 종목이 다음 배치까지 옛 값에 묶인다.
    전량 실패(네트워크 장애 등)나 기존 파일 파싱 실패 시에는 저장하지 않는다 — 기존 스냅샷 보존.
    반환값은 이번 수집 결과(build_market_snapshot — 대상 종목만, ticker_count·failed 포함).
    """
    fresh = build_market_snapshot(market, tickers, throttle_sec=throttle_sec)
    if fresh['ticker_count'] == 0 and tickers:
        return fresh
    failed = set(fresh['failed'])
    with _snap_lock:   # 수집(수 분)은 락 밖, 병합·저장만 안에서 — 그 사이 미스 병합분도 살린다
        try:
            latest = _read_snapshot(market)
        except _UnreadableSnapshot as e:
            print(f'[{market}] 스냅샷을 읽을 수 없어 재수집분 저장 생략 — 파일은 그대로 둔다 ({e})')
            return fresh
        data = dict(latest.get('data', {}))
        for t, rec in fresh['data'].items():
            # 실패 종목의 fresh 레코드는 수집 시작 때 읽은 옛 값 — 그 사이 더 새 값이 들어왔으면 유지
            if t not in failed or t not in data:
                data[t] = rec
        if set(latest.get('data', {})) <= set(tickers):
            snap = {**fresh, 'data': data}                       # 전 종목 수집 — 배치와 같은 의미
        else:
            snap = {**latest, 'market': market, 'data': data,
                    'partial_refetched_at': datetime.now(KST).isoformat(timespec='seconds')}
        save_snapshot(market, snap)
    return fresh


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
        try:
            merged[name] = _df_to_records(
                _safe_merge(df, merged.get(name), INDEX_DAYS, name))
        except Exception as e:   # 지수 1개의 변환 실패가 나머지 지수까지 미저장시키면 안 된다
            print(f'[{name}] 레코드 변환 실패({type(e).__name__}: {e}) — 기존 값 유지')

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
