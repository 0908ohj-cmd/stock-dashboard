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
