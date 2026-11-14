#!/usr/bin/env python3
"""장 마감 후 OHLCV 스냅샷 배치 수집 (GitHub Actions cron에서 실행).

사용법: python scripts/fetch_snapshot.py --markets kr|us|all
휴장일에도 실행된다 — 데이터가 안 변해도 fetched_at 갱신이 신선도 판정의 근거.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import store

# 시장별 종목 = 티커 파일 합집합. 정의는 data/store.py 한 곳 — 앱 사이드바 🔄 데이터 재수집도
# 같은 목록으로 전 종목을 다시 받는다 (scripts를 앱이 import하지 않도록 data/ 계층에 둔다)
TICKER_FILES = store.SNAPSHOT_TICKER_FILES
MARKET_GROUPS = {
    'kr':  ['KR_KOSPI', 'KR_KOSDAQ'],
    'us':  ['US'],
    'all': ['KR_KOSPI', 'KR_KOSDAQ', 'US'],
}
MIN_SUCCESS_RATE = 0.7
# 종목 마지막 거래일이 같은 배치에서 받은 시장 지수보다 늦으면(소스 지연) 기다렸다 다시 받는다.
# 2026-11-10 US: NASDAQ 지수는 11/09, 종목 일괄 다운로드는 11/06까지만 → 하루 낡은 채 저장됐다.
MARKET_INDEX = {'KR_KOSPI': 'KOSPI', 'KR_KOSDAQ': 'KOSDAQ', 'US': 'NASDAQ'}
LAG_RETRY_WAITS_SEC = (300, 900)
# 종목은 저장했지만 지수 수집이 실패했다 — run_batch_local.sh가 종목을 올린 뒤 실패로 끝내
# job_wrapper 알림·/jobs 실패 기록·catchup 재시도로 이어지게 한다.
INDEX_FAILED_EXIT = 3


load_tickers = store.snapshot_tickers


def _index_last(market: str) -> str | None:
    rec = store.load_snapshot('indices').get('data', {}).get(MARKET_INDEX.get(market, ''))
    return rec['dates'][-1] if rec and rec.get('dates') else None


def run_market(market: str, waits=LAG_RETRY_WAITS_SEC, sleep=time.sleep) -> bool:
    """수집 → 성공률 게이트 통과 시에만 전체 교체 저장. 통과 여부 반환.
    종목이 시장 지수보다 늦으면 waits 간격으로 다시 받는다(그래도 늦으면 경고 후 저장)."""
    tickers = load_tickers(market)
    if not tickers:
        print(f'[{market}] 티커 파일 없음 — 건너뜀')
        return True
    snap = store.build_market_snapshot(market, tickers)
    want = _index_last(market)
    for wait in waits:
        have = snap.get('last_trading_date')
        if not want or not have or have >= want:
            break
        print(f'[{market}] 종목 마지막 거래일 {have} < {MARKET_INDEX[market]} {want} — {wait}초 뒤 재수집')
        sleep(wait)
        snap = store.build_market_snapshot(market, tickers)
    if want and snap.get('last_trading_date') and snap['last_trading_date'] < want:
        print(f"[{market}] ⚠️ 재시도 후에도 종목 {snap['last_trading_date']} < 지수 {want} — "
              '폴백 배치가 다시 받는다 (check_snapshot_fresh)')
    # 분모는 실제 시도 수(성공+실패) — 티커 파일 중복 라인에 왜곡되지 않는다
    attempted = snap['ticker_count'] + len(snap['failed'])
    rate = snap['ticker_count'] / attempted if attempted else 1.0
    if rate < MIN_SUCCESS_RATE:
        print(f'[{market}] 성공률 {rate:.0%} < {MIN_SUCCESS_RATE:.0%} — 스냅샷 미교체')
        return False
    store.save_snapshot(market, snap)
    print(f"[{market}] {snap['ticker_count']}/{attempted}개 저장 "
          f"(실패 {len(snap['failed'])}: {snap['failed'][:5]})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--markets', choices=['kr', 'us', 'all'], default='all')
    args = ap.parse_args()

    # 지수를 먼저 받는다 — 종목이 지수보다 늦은지(소스 지연) 판정하는 기준이다
    idx = store.refetch_indices(args.markets) or {}
    if idx.get('failed'):
        print(f"[indices] ⚠️ 지수 수집 실패: {', '.join(idx['failed'])} — 기존 값 유지")
    else:
        print(f'[indices] {args.markets} 지수 갱신')
    results = [run_market(m) for m in MARKET_GROUPS[args.markets]]  # all() 단락 방지
    if not all(results):
        return 1
    return INDEX_FAILED_EXIT if idx.get('failed') else 0


if __name__ == '__main__':
    sys.exit(main())
