"""
섹터(테마) 캐시 일괄 갱신 CLI — claude CLI가 있는 로컬에서 실행.

기본: 미캐시 종목만 분류. --stale-days N은 updated가 N일 지난 종목 포함,
--all은 전량 재분류. 대상 = KR 시총 상위 200×2 + US 유니버스 + 업로드 저장분
+ 리더보드. 캐시 저장은 마지막 1회 (병렬 중 경합 없음).
"""
import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

from data import theme_classifier               # noqa: E402
from data.sector import _kr_names, _load_themes  # noqa: E402


def _collect_universe() -> list:
    """분류 대상 티커 전체 수집 (중복 제거). 유니버스 소스 하나가 죽어도 나머지는 진행."""
    tickers: set = set()

    from data import universe
    get_kr = getattr(universe.get_kr_universe, '__wrapped__', universe.get_kr_universe)
    get_us = getattr(universe.get_us_universe, '__wrapped__', universe.get_us_universe)
    for market in ('KR_KOSPI', 'KR_KOSDAQ'):
        try:
            tickers.update(get_kr(market))
        except Exception as e:
            print(f'⚠️ {market} 유니버스 수집 실패: {e}')
    try:
        tickers.update(get_us())
    except Exception as e:
        print(f'⚠️ US 유니버스 수집 실패: {e}')

    for f in sorted((_REPO / 'data' / 'saved').glob('*.tickers')):
        tickers.update(line.strip() for line in f.read_text(encoding='utf-8').splitlines()
                       if line.strip())

    from data import leaderboard_store
    for m in ('kr', 'us'):
        tickers.update(leaderboard_store.get_tickers(m))

    return sorted(tickers)


def _select_targets(tickers: list, cache: dict, all_mode: bool, stale_days) -> list:
    if all_mode:
        return list(tickers)
    cutoff = datetime.now() - timedelta(days=stale_days) if stale_days else None
    out = []
    for t in tickers:
        entry = cache.get(t)
        if not isinstance(entry, dict) or not (entry.get('detail') or entry.get('theme')):
            out.append(t)
            continue
        if cutoff is not None:
            try:
                updated = datetime.fromisoformat(entry['updated'])
            except Exception:
                out.append(t)
                continue
            if updated < cutoff:
                out.append(t)
    return out


def _classify_targets(targets: list, themes: list, names: dict, workers: int) -> list:
    """병렬 1차 분류 → '기타' 배치 resolve → 캐시 1회 저장. 완전 실패 티커 목록 반환."""
    cache = theme_classifier._load_cache()
    results: dict = {}

    def _one(ticker):
        first = theme_classifier._classify_one(ticker, names.get(ticker), themes)
        if first is None:  # classify()와 동일하게 1회 재시도
            first = theme_classifier._classify_one(ticker, names.get(ticker), themes)
        return first

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_one, t): t for t in targets}
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            results[ticker] = future.result()
            r = results[ticker]
            label = (r.get('detail') or r.get('theme')) if r else '실패'
            print(f'[{i}/{len(targets)}] {ticker} → {label}', flush=True)

    now = datetime.now().isoformat()
    for ticker, r in results.items():
        if r is not None and r.get('theme') != '기타':
            cache[ticker] = {**r, 'updated': now}

    # '기타'+detail 후보 → 20개 배치 resolve (classify()와 동일 정책:
    # resolve 실패분도 '기타'로 캐시해 자가치유 대상으로 남긴다)
    misc = [t for t, r in results.items()
            if r is not None and r.get('theme') == '기타' and r.get('detail')]
    current = {t: results[t] for t in misc}
    for i in range(0, len(misc), 20):
        batch = misc[i:i + 20]
        resolved = theme_classifier._resolve_misc(batch, current, themes)
        for ticker in batch:
            cache[ticker] = {**resolved.get(ticker, results[ticker]), 'updated': now}

    theme_classifier._save_cache(cache)
    return sorted(t for t, r in results.items() if r is None)


def main() -> int:
    parser = argparse.ArgumentParser(description='섹터(테마) 캐시 일괄 갱신')
    parser.add_argument('--all', action='store_true', help='전량 재분류')
    parser.add_argument('--stale-days', type=int, default=None,
                        help='updated가 N일 지난 종목도 재분류 (기본: 미캐시만)')
    parser.add_argument('--workers', type=int, default=4, help='병렬 워커 수 (기본 4)')
    args = parser.parse_args()

    themes, overrides = _load_themes()
    cache = theme_classifier._load_cache()

    tickers = [t for t in _collect_universe() if t not in overrides]
    targets = _select_targets(tickers, cache, args.all, args.stale_days)
    print(f'대상 {len(targets)}/{len(tickers)}종목 (워커 {args.workers})')
    if not targets:
        print('갱신할 종목 없음')
        return 0

    names = _kr_names([t for t in targets if t.isdigit()])
    failed = _classify_targets(targets, themes, names, args.workers)

    final = theme_classifier._load_cache()
    dist = Counter(v.get('theme', '기타') for v in final.values() if isinstance(v, dict))
    print('\n=== theme 분포 (캐시 전체) ===')
    for theme, n in dist.most_common():
        print(f'{n:4d}  {theme}')
    if failed:
        print(f'\n⚠️ 완전 실패 {len(failed)}종목 (미캐시 유지 → 다음 실행 때 재시도): {", ".join(failed)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
