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

TICKER_FILES = {
    'KR_KOSPI':  ['kospi.tickers',  'kospi_10ema.tickers'],
    'KR_KOSDAQ': ['kosdaq.tickers', 'kosdaq_10ema.tickers'],
    # us_sector.tickers = 주도섹터 유니버스 210종목 + 벤치마크 QQQ·IWM (data/universe/us_innovation_210.json)
    'US':        ['us.tickers',     'us_10ema.tickers', 'us_sector.tickers'],
}
MARKET_GROUPS = {
    'kr':  ['KR_KOSPI', 'KR_KOSDAQ'],
    'us':  ['US'],
    'all': ['KR_KOSPI', 'KR_KOSDAQ', 'US'],
}
MIN_SUCCESS_RATE = 0.7
# 종목과 같은 배치에서 받은 시장 지수의 마지막 날짜가 어긋나면(소스 지연) 기다렸다 늦은 쪽을 다시 받는다.
#   종목이 늦다: 2026-11-10 US — NASDAQ 지수는 11/09, 종목 일괄 다운로드는 11/06까지만 → 종목 재수집
#   지수가 늦다: 2026-11-11·11-12 US — 종목 11-10·11-11, NASDAQ 11-09·11-10 (#24) → 지수 재수집
MARKET_INDEX = {'KR_KOSPI': 'KOSPI', 'KR_KOSDAQ': 'KOSDAQ', 'US': 'NASDAQ'}
INDEX_GROUP = {'KR_KOSPI': 'kr', 'KR_KOSDAQ': 'kr', 'US': 'us'}   # store.refetch_indices 인자
LAG_RETRY_WAITS_SEC = (300, 900)
# 종목은 저장했지만 지수 수집이 실패했다 — run_batch_local.sh가 종목을 올린 뒤 실패로 끝내
# job_wrapper 알림·/jobs 실패 기록·catchup 재시도로 이어지게 한다.
# 기준은 '이번 배치에서 한 번도 못 받은 지수' — 첫 수집이 실패해도 run_market의 지수 재수집(#24)이
# 성공하면 실패가 아니다.
INDEX_FAILED_EXIT = 3


def load_tickers(market: str) -> list:
    saved = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'saved'
    seen, result = set(), []
    for filename in TICKER_FILES[market]:
        path = saved / filename
        if not path.exists():
            continue
        for t in path.read_text(encoding='utf-8').splitlines():
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                result.append(t)
    return result


def _index_last(market: str) -> str | None:
    rec = store.load_snapshot('indices').get('data', {}).get(MARKET_INDEX.get(market, ''))
    return rec['dates'][-1] if rec and rec.get('dates') else None


def run_market(market: str, waits=LAG_RETRY_WAITS_SEC, sleep=time.sleep,
               index_failed: set | None = None) -> bool:
    """수집 → 성공률 게이트 통과 시에만 전체 교체 저장. 통과 여부 반환.
    종목과 시장 지수의 마지막 날짜가 어긋나면 waits 간격으로 늦은 쪽을 다시 받는다
    (종목이 늦으면 종목, 지수가 늦으면 지수). 그래도 어긋나면 경고 후 저장 — 폴백 게이트가 잡는다.
    index_failed: 아직 못 받은 지수 집합(main 소유). 지수 재수집에 성공한 지수는 여기서 뺀다."""
    tickers = load_tickers(market)
    if not tickers:
        print(f'[{market}] 티커 파일 없음 — 건너뜀')
        return True
    idx_name = MARKET_INDEX[market]
    snap = store.build_market_snapshot(market, tickers)
    want = _index_last(market)
    for wait in waits:
        have = snap.get('last_trading_date')
        if not want or not have or have == want:
            break
        if have < want:
            print(f'[{market}] 종목 마지막 거래일 {have} < {idx_name} {want} — {wait}초 뒤 종목 재수집')
            sleep(wait)
            snap = store.build_market_snapshot(market, tickers)
        else:
            print(f'[{market}] {idx_name} 마지막 날짜 {want} < 종목 {have} — {wait}초 뒤 지수 재수집')
            sleep(wait)
            res = store.refetch_indices(INDEX_GROUP[market])
            if index_failed is not None and res:
                group = {MARKET_INDEX[m] for m in MARKET_GROUPS[INDEX_GROUP[market]]}
                index_failed -= group - set(res.get('failed', []))
            want = _index_last(market)
    have = snap.get('last_trading_date')
    if want and have and have < want:
        print(f'[{market}] ⚠️ 재시도 후에도 종목 {have} < 지수 {want} — '
              '폴백 배치가 다시 받는다 (check_snapshot_fresh)')
    elif want and have and want < have:
        print(f'[{market}] ⚠️ 재시도 후에도 {idx_name} {want} < 종목 {have} — '
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

    # 지수를 먼저 받는다 — 종목과 지수 중 어느 쪽이 늦은지(소스 지연) 판정하는 기준이다
    idx = store.refetch_indices(args.markets) or {}
    index_failed = set(idx.get('failed', []))
    if index_failed:
        # '지수 수집 실패: ' 문구는 최종 판정에만 쓴다 — run_batch_local.sh가 그 문구를 실패로 집는다
        print(f"[indices] ⚠️ 첫 수집에서 못 받은 지수 {', '.join(sorted(index_failed))} — "
              '기존 값 유지, 종목보다 늦으면 재수집')
    else:
        print(f'[indices] {args.markets} 지수 갱신')
    results = [run_market(m, index_failed=index_failed)
               for m in MARKET_GROUPS[args.markets]]  # all() 단락 방지
    if not all(results):
        return 1
    if index_failed:
        # run_batch_local.sh가 이 문구('지수 수집 실패: 이름')의 마지막 줄에서 실패 지수명을 가져간다
        print(f"[indices] ⚠️ 지수 수집 실패: {', '.join(sorted(index_failed))} — 기존 값 유지")
        return INDEX_FAILED_EXIT
    return 0


if __name__ == '__main__':
    sys.exit(main())
