"""
yfinance 대분류(섹터) 캐시 일괄 수집.

theme_cache.json(LLM 테마)과 짝이 되는 yf_sector_cache.json을 채운다. LLM과 달리 사용량 상한이
없어 한 번에 전량 수집이 가능하다. 갱신은 신규 종목 유입 시에만 필요(industry는 거의 불변).

    python3 scripts/refresh_yf_sectors.py            # 미캐시분만
    python3 scripts/refresh_yf_sectors.py --all      # 전량 재수집

대상 = data/saved의 현행 티커 파일 합집합(store.snapshot_tickers). KRX 유니버스(pykrx)·S&P500·
리더보드는 조회하지 않는다(#45 M19).
"""
import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

from data import store, yf_sector  # noqa: E402


def _collect_targets() -> list:
    """(ticker, market) 쌍 — data/saved의 현행 티커 파일(store.snapshot_tickers) 합집합, 티커순.

    KR은 시장별 접미사가 달라 market을 함께 들고 다닌다. 시장은 티커 파일이 정한다 — 예전엔
    isdigit()로 판정해 영숫자 KR 코드(0015S0)를 미국 종목으로 조회했다(#45 L6). 예전엔 라운드
    (refresh_yf_sectors.sh, 최대 30회)마다 KRX 유니버스(pykrx)·S&P500·리더보드까지 다시 모았다(#45 M19).
    """
    pairs: dict = {}
    for market in ('KR_KOSPI', 'KR_KOSDAQ', 'US'):
        for t in store.snapshot_tickers(market):
            pairs.setdefault(t, market)
    return sorted(pairs.items())


def main() -> int:
    parser = argparse.ArgumentParser(description='yfinance 대분류 캐시 수집')
    parser.add_argument('--all', action='store_true', help='캐시 무시하고 전량 재수집')
    parser.add_argument('--workers', type=int, default=8, help='병렬 워커 수 (기본 8)')
    args = parser.parse_args()

    # 엄격 읽기(#26) — _load_cache는 파싱 실패를 {}로 돌려줘, 그 위에 저장하면 캐시 전체가 이번 회차 결과로 덮인다
    cache = yf_sector._read_cache()
    if cache is None:
        print(f'⛔ {yf_sector._CACHE_FILE} 파싱 실패 — 덮어쓰지 않고 중단합니다. '
              '파일을 git에서 되살리거나 고친 뒤 다시 실행하세요.', file=sys.stderr)
        return 2
    pairs = _collect_targets()
    targets = pairs if args.all else [(t, m) for t, m in pairs if not cache.get(t)]
    print(f'대상 {len(targets)}/{len(pairs)}종목 (워커 {args.workers})')
    if not targets:
        print('수집할 종목 없음')
        return 0

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(yf_sector.fetch_label, t, m): t for t, m in targets}
        for future in as_completed(futures):
            ticker = futures[future]
            done += 1
            try:
                label = future.result()
            except Exception:
                label = ''
            if label:
                cache[ticker] = label
            if done % 100 == 0 or done == len(targets):
                print(f'[{done}/{len(targets)}] {ticker} → {label or "실패"}', flush=True)

    yf_sector._save_cache(cache)

    dist = Counter(cache.values())
    print(f'\n=== 대분류 분포 (캐시 {len(cache)}종목) ===')
    for label, n in dist.most_common(25):
        print(f'{n:5d}  {label}')
    failed = [t for t, _ in targets if not cache.get(t)]
    if failed:
        print(f'\n⚠️ 조회 실패 {len(failed)}종목: {", ".join(failed[:40])}'
              f'{" …" if len(failed) > 40 else ""}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
