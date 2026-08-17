"""
섹터(테마) 어댑터 — data.theme_classifier로 위임하고 WL 표시용 detail만 추출.
(stockEdge watchlist/theme_classifier.py와 같은 역할. 구 자체 프롬프트·yfinance/pykrx
폴백은 폐기 — 폴백 라벨이 캐시에 영구 고착되는 오염의 원인이었다.)
"""
import json
from pathlib import Path

from data import theme_classifier

_NAMES_FILE = Path(__file__).parent / 'kr_names.json'

# 이번 프로세스에서 분류를 시도한 티커 — 실패('기타')의 반복 재시도를 세션 내에서만 막고,
# 재시작하면 다시 기회를 준다 (실패는 디스크에 저장되지 않으므로)
_SESSION_ATTEMPTED: set = set()


def _load_themes() -> tuple[list, dict]:
    data = json.loads(theme_classifier._THEMES_FILE.read_text(encoding='utf-8'))
    return data['known_themes'], dict(data.get('ticker_overrides', {}))


def _kr_names(tickers: list) -> dict:
    """번들 kr_names.json에서 종목명 힌트 추출 (미보유 종목은 힌트 없이 진행)."""
    try:
        bundle = json.loads(_NAMES_FILE.read_text(encoding='utf-8'))
    except Exception:
        bundle = {}
    return {t: bundle[t] for t in tickers if t in bundle}


def _label(entry) -> str:
    if not isinstance(entry, dict):
        return '기타'
    return entry.get('detail') or entry.get('theme') or '기타'


def get_sectors(tickers: list, market: str) -> dict:
    """{ticker: 표시 라벨(detail 우선, theme 폴백)}.

    캐시 보유분은 TTL과 무관하게 사용(렌더 무블로킹 — 갱신은 scripts/refresh_sectors.sh 몫),
    미보유분만 분류를 시도한다. claude CLI가 없는 환경(배포 서버)에서는 분류가 즉시
    실패해 '기타'로 표시된다.
    """
    cache = theme_classifier._load_cache()
    themes, overrides = _load_themes()
    result = {}
    to_classify = []

    for ticker in tickers:
        entry = cache.get(ticker)
        if ticker in overrides:
            result[ticker] = overrides[ticker]
        elif isinstance(entry, dict) and (entry.get('detail') or entry.get('theme')):
            result[ticker] = _label(entry)
        elif ticker in _SESSION_ATTEMPTED:
            result[ticker] = '기타'
        else:
            to_classify.append(ticker)

    if to_classify:
        _SESSION_ATTEMPTED.update(to_classify)
        names = _kr_names(to_classify) if market.startswith('KR') else None
        full = theme_classifier.classify(to_classify, themes, overrides=overrides, names=names)
        for ticker in to_classify:
            result[ticker] = _label(full.get(ticker))

    return result


def get_major_themes(tickers: list) -> dict:
    """{ticker: LLM 대분류(theme)}. 캐시만 읽고 분류를 트리거하지 않는다.

    표시용은 detail(get_sectors)이지만, yfinance가 industry를 주지 않는 종목의 '섹터' 컬럼을
    메우는 데 이 대분류가 쓰인다(data.yf_sector.resolve_sector).
    """
    cache = theme_classifier._load_cache()
    result = {}
    for ticker in tickers:
        entry = cache.get(ticker)
        result[ticker] = entry.get('theme', '') if isinstance(entry, dict) else ''
    return result


def get_sectors_cached_only(tickers: list, market: str) -> dict:
    """캐시에 있는 라벨만 반환, 미캐시는 '기타' — 분류(subprocess·네트워크)를 절대
    트리거하지 않으므로 as-of 재계산처럼 블로킹이 허용되지 않는 경로에서 사용."""
    cache = theme_classifier._load_cache()
    _, overrides = _load_themes()
    return {
        t: overrides[t] if t in overrides else _label(cache.get(t))
        for t in tickers
    }
