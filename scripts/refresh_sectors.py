"""
구 섹터(테마) 캐시(theme_cache.json) 일괄 갱신 CLI — claude CLI가 있는 로컬에서 실행.

구 체계 폴백용이다. 신규 종목 분류는 배치의 scripts/classify_themes.py가 맡는다.
기본: 미캐시 종목만 분류. --stale-days N은 updated가 N일 지난 종목 포함,
--all은 전량 재분류 — 둘 다 LLM WebSearch를 종목 수만큼 부르므로 명시할 때만.
대상 = data/saved의 현행 티커 파일 합집합(store.snapshot_tickers). KRX 유니버스(pykrx)·S&P500·
리더보드는 조회하지 않는다(#45 M19). 캐시 저장은 마지막 1회 (병렬 중 경합 없음).
"""
import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO))

from data import store, theme_classifier        # noqa: E402
from data.sector import _kr_names, _load_themes  # noqa: E402


class CacheUnreadable(RuntimeError):
    """theme_cache.json이 있는데 파싱이 안 된다 — 그 위에 저장하면 캐시 전체가 이번 회차 결과로 덮인다."""


def _read_cache_strict() -> dict:
    """캐시를 엄격하게 읽는다(theme_classifier._read_cache — 파일 없음 {}, 파싱 실패 None, #26).
    _load_cache는 파싱 실패를 {}로 돌려줘 저장 경로에서 쓰면 깨진 파일을 덮어쓴다."""
    cache = theme_classifier._read_cache()
    if cache is None:
        raise CacheUnreadable(f'{theme_classifier._CACHE_FILE} 파싱 실패 — 덮어쓰지 않고 중단합니다. '
                              '파일을 git에서 되살리거나 고친 뒤 다시 실행하세요.')
    return cache


def _collect_targets() -> list:
    """(ticker, market) 쌍 — data/saved의 현행 티커 파일(store.snapshot_tickers) 합집합, 티커순.

    예전엔 KRX 시총 상위 200×2(get_kr_universe.__wrapped__로 st 캐시를 우회해 pykrx 직접 조회)·
    S&P500·리더보드까지 모아 화면에 없는 종목까지 1400여 종목을 분류했다(#45 M19).
    여러 시장 파일에 있으면 먼저 나온 시장.
    """
    pairs: dict = {}
    for market in ('KR_KOSPI', 'KR_KOSDAQ', 'US'):
        for t in store.snapshot_tickers(market):
            pairs.setdefault(t, market)
    return sorted(pairs.items())


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


def _classify_targets(targets: list, themes: list, names: dict, workers: int, markets: dict) -> list:
    """병렬 1차 분류 → '기타' 배치 resolve → 캐시 1회 저장. 완전 실패 티커 목록 반환.

    markets = {ticker: 시장} — 프롬프트가 KR/US를 코드 모양이 아니라 시장으로 알린다(#45 L6).
    캐시 파일이 깨져 있으면 분류 전에 CacheUnreadable — 저장하지 않는다."""
    cache = _read_cache_strict()
    results: dict = {}

    def _one(ticker):
        first = theme_classifier._classify_one(ticker, names.get(ticker), themes, markets[ticker])
        if first is None:  # 일시적 실패 대비 1회 재시도
            first = theme_classifier._classify_one(ticker, names.get(ticker), themes, markets[ticker])
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

    # '기타'+detail 후보 → 20개 배치 resolve. resolve 실패분도 '기타'로 캐시해 자가치유 대상으로 남긴다.
    # detail 없이 완전 실패한 종목(None)은 넘기지 않는다 — 티커 철자만 보고 대분류를 지어낸다(FROG 사례)
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

    try:
        cache = _read_cache_strict()
    except CacheUnreadable as e:
        print(f'⛔ {e}', file=sys.stderr)
        return 2
    themes, overrides = _load_themes()

    markets = dict(_collect_targets())
    tickers = [t for t in markets if t not in overrides]
    targets = _select_targets(tickers, cache, args.all, args.stale_days)
    print(f'대상 {len(targets)}/{len(tickers)}종목 (워커 {args.workers})')
    if not targets:
        print('갱신할 종목 없음')
        return 0

    names = _kr_names([t for t in targets if markets[t].startswith('KR')])   # 영숫자 KR 코드 포함 (#45 L6)
    failed = _classify_targets(targets, themes, names, args.workers, markets)

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
