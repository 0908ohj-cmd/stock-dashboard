"""
yfinance 대분류(섹터) 캐시 일괄 수집.

theme_cache.json(LLM 테마)과 짝이 되는 yf_sector_cache.json을 채운다. LLM과 달리 사용량 상한이
없어 한 번에 전량 수집이 가능하다. 갱신은 신규 종목 유입 시에만 필요(industry는 거의 불변).

    python3 scripts/refresh_yf_sectors.py            # 미캐시분만
    python3 scripts/refresh_yf_sectors.py --all      # 전량 재수집
"""
import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

from data import yf_sector  # noqa: E402


def _collect_targets() -> list:
    """(ticker, market) 쌍 수집. KR은 시장별 접미사가 달라 market을 함께 들고 다닌다."""
    pairs = {}

    from data import universe
    get_kr = getattr(universe.get_kr_universe, '__wrapped__', universe.get_kr_universe)
    get_us = getattr(universe.get_us_universe, '__wrapped__', universe.get_us_universe)
    for market in ('KR_KOSPI', 'KR_KOSDAQ'):
        try:
            for t in get_kr(market):
                pairs.setdefault(t, market)
        except Exception as e:
            print(f'⚠️ {market} 유니버스 수집 실패: {e}')
    try:
        for t in get_us():
            pairs.setdefault(t, 'US')
    except Exception as e:
        print(f'⚠️ US 유니버스 수집 실패: {e}')

    for f in sorted((_REPO / 'data' / 'saved').glob('*.tickers')):
        name = f.name.lower()
        market = ('KR_KOSDAQ' if 'kosdaq' in name else
                  'KR_KOSPI' if 'kospi' in name or '_kr' in name else 'US')
        for line in f.read_text(encoding='utf-8').splitlines():
            t = line.strip()
            if t:
                pairs.setdefault(t, market if t.isdigit() else 'US')

    from data import leaderboard_store
    for m, market in (('kr', 'KR_KOSPI'), ('us', 'US')):
        for t in leaderboard_store.get_tickers(m):
            pairs.setdefault(t, market if t.isdigit() else 'US')

    return sorted(pairs.items())


def main() -> int:
    parser = argparse.ArgumentParser(description='yfinance 대분류 캐시 수집')
    parser.add_argument('--all', action='store_true', help='캐시 무시하고 전량 재수집')
    parser.add_argument('--workers', type=int, default=8, help='병렬 워커 수 (기본 8)')
    args = parser.parse_args()

    cache = yf_sector._load_cache()
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
