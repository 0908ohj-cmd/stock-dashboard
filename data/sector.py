"""
섹터(테마) 어댑터 — 와치리스트 '테마'·'섹터' 컬럼 라벨. 새 분류(data/classification)를 먼저 쓰고,
기록이 없는 종목만 구 캐시(theme_cache.json — data.theme_classifier)로 폴백한다.
캐시만 읽는다 — 렌더 경로에서 LLM 분류를 돌리지 않는다(#45 M18). 신규 분류는 배치 몫.
"""
import json
import logging
from pathlib import Path

from data import classification, theme_classifier

_log = logging.getLogger(__name__)
_LOGGED: set = set()   # 같은 실패를 종목마다·렌더마다 반복해 찍지 않는다

_NAMES_FILE = Path(__file__).parent / 'kr_names.json'

# 렌더 경로 런타임 분류(#45 M18에서 제거)가 쓰던 세션 가드 — 지금은 쓰이지 않는다. 정리는 #46
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
    """{ticker: 표시 라벨} — 새 분류(detail 우선) → ticker_overrides → 구 캐시(detail, theme) → '기타'.

    캐시만 읽는다. 예전엔 기록이 없는 종목마다 렌더 중에 claude CLI 분류(종목당 최대 90초 × 2)를
    돌렸고, '기타' 묶음 resolve가 themes.json에 새 대분류를 자동 추가했다 — 신 체계의 "LLM은 새
    테마를 만들지 못한다" 원칙과 어긋난다(#45 M18). 신규 종목 분류는 배치의
    scripts/classify_themes.py만 맡는다.
    """
    return get_sectors_cached_only(tickers, market)


def _warn_once(market: str, ticker: str, e: Exception) -> None:
    key = (market, type(e).__name__, str(e))
    if key in _LOGGED:
        return
    _LOGGED.add(key)
    _log.warning('[sector] %s %s 새 분류 읽기 실패(%s: %s) — 구 라벨로 대체 (같은 사유는 이후 생략)',
                 market, ticker, type(e).__name__, e)


def _verified_label(market: str, ticker: str) -> str | None:
    """새 분류 저장소(data/classification)의 라벨. 기록이 있으면 미분류라도 '미분류'를 쓰고,
    기록이 없을 때만 None → 구 캐시 사용."""
    try:
        return classification.display_label(market, ticker)
    except Exception as e:
        _warn_once(market, ticker, e)
        return None


def _verified_major(market: str, ticker: str) -> str:
    try:
        return classification.display_major(market, ticker) or ''
    except Exception as e:
        _warn_once(market, ticker, e)
        return ''


def get_display_sectors(tickers: list, market: str, yf_secs: dict) -> dict:
    """와치리스트 '섹터' 컬럼 — 주도섹터 탭과 같은 기준(주력 테마)을 먼저 쓴다. 미분류 기록은 '미분류'.
    분류 기록이 아예 없는 종목만 예전 방식(yfinance 산업분류 → 구 LLM 대분류)으로 채운다."""
    from data.yf_sector import resolve_sector
    old = get_major_themes(tickers, market)
    return {t: _verified_major(market, t) or resolve_sector(yf_secs.get(t, ''), old.get(t, ''))
            for t in tickers}


def get_major_themes(tickers: list, market: str = 'US') -> dict:
    """{ticker: 대분류}. 새 분류 저장소의 주력 테마를 먼저 쓰고(미분류 기록은 '미분류'),
    기록이 없으면 구 LLM 캐시.
    캐시만 읽고 분류를 트리거하지 않는다.

    표시용은 detail(get_sectors)이지만, yfinance가 industry를 주지 않는 종목의 '섹터' 컬럼을
    메우는 데 이 대분류가 쓰인다(data.yf_sector.resolve_sector).
    """
    cache = theme_classifier._load_cache()
    result = {}
    for ticker in tickers:
        verified = _verified_major(market, ticker)
        if verified:
            result[ticker] = verified
            continue
        entry = cache.get(ticker)
        result[ticker] = entry.get('theme', '') if isinstance(entry, dict) else ''
    return result


def get_sectors_cached_only(tickers: list, market: str) -> dict:
    """캐시에 있는 라벨만 반환, 미캐시는 '기타' — 분류(subprocess·네트워크)를 절대
    트리거하지 않으므로 as-of 재계산처럼 블로킹이 허용되지 않는 경로에서 사용."""
    cache = theme_classifier._load_cache()
    _, overrides = _load_themes()
    return {
        t: _verified_label(market, t) or (overrides[t] if t in overrides else _label(cache.get(t)))
        for t in tickers
    }
