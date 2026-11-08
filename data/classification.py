"""종목 투자테마 분류 저장소 (주력 1개 + 보조 태그 + 신뢰도 + 근거).

data/taxonomy/{market}.json — 고정 테마 목록(버전 관리). LLM이 새 테마를 만들지 못한다.
data/classification/{market}.json — 종목별 분류 기록.

규칙 (마스터 명세 2.3):
- 근거 URL이 없거나 신뢰도 low면 primaryTheme은 UNCLASSIFIED로 두고 제안값만 suggestedTheme에 남긴다.
- overrideByUser=True인 기록은 자동 분류가 덮어쓰지 않는다.
- 이전 기록은 history에 남긴다 (분류 변경 이력).
Streamlit 무의존.
"""
import json
import os
import pathlib
from datetime import datetime, timedelta, timezone

_BASE = pathlib.Path(__file__).parent
TAXONOMY_DIR = _BASE / 'taxonomy'
STORE_DIR = _BASE / 'classification'
KST = timezone(timedelta(hours=9))

UNCLASSIFIED = 'UNCLASSIFIED'
CONFIDENCE = ('high', 'medium', 'low', 'unverified')
_HISTORY_KEEP = 10


def market_key(market: str) -> str:
    """'KR_KOSPI'/'KR_KOSDAQ' → 'kr', 'US' → 'us'."""
    return 'kr' if market.upper().startswith('KR') else 'us'


def load_taxonomy(market: str) -> dict:
    return json.loads((TAXONOMY_DIR / f'{market_key(market)}.json').read_text(encoding='utf-8'))


def theme_index(market: str) -> dict:
    """{code: theme dict}."""
    return {t['code']: t for t in load_taxonomy(market)['themes']}


def _store_path(market: str) -> pathlib.Path:
    return STORE_DIR / f'{market_key(market)}.json'


_cache: dict = {}


def load_store(market: str) -> dict:
    """{ticker: record}. 파일 mtime이 바뀌면 다시 읽는다."""
    path = _store_path(market)
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime_ns
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    data = json.loads(path.read_text(encoding='utf-8')).get('records', {})
    _cache[path] = (mtime, data)
    return data


def save_store(market: str, records: dict) -> pathlib.Path:
    STORE_DIR.mkdir(exist_ok=True)
    path = _store_path(market)
    tax = load_taxonomy(market)
    body = {
        'market': market_key(market),
        'taxonomyVersion': tax['version'],
        'updatedAt': datetime.now(KST).isoformat(timespec='seconds'),
        'records': dict(sorted(records.items())),
    }
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)
    _cache.pop(path, None)
    return path


def normalize(market: str, raw: dict, method: str) -> dict:
    """LLM/수동 결과를 저장 형식으로 정규화하고 승인 게이트를 적용한다."""
    themes = theme_index(market)
    primary = str(raw.get('primaryTheme') or '').strip().upper()
    secondary = []
    for s in raw.get('secondaryThemes') or []:
        s = str(s).strip().upper()
        if s in themes and s != primary and s != UNCLASSIFIED and s not in secondary:
            secondary.append(s)
    urls = [u for u in (raw.get('evidenceUrls') or [])
            if isinstance(u, str) and u.startswith(('http://', 'https://'))]
    conf = str(raw.get('confidence') or '').strip().lower()
    if conf not in CONFIDENCE:
        conf = 'unverified'

    gate = []
    if primary not in themes:
        gate.append(f'테마 목록에 없는 코드: {primary or "(빈 값)"}')
    if not urls:
        gate.append('근거 URL 없음')
    if conf in ('low', 'unverified'):
        gate.append(f'신뢰도 {conf}')

    accepted = not gate and primary != UNCLASSIFIED
    return {
        'companyName': str(raw.get('companyName') or '').strip(),
        'primaryTheme': primary if accepted else UNCLASSIFIED,
        'suggestedTheme': None if accepted else (primary if primary in themes else None),
        'secondaryThemes': secondary,
        'detail': str(raw.get('detail') or '').strip(),
        'businessSummary': str(raw.get('businessSummary') or '').strip(),
        'confidence': conf if accepted else ('low' if conf == 'low' else 'unverified'),
        'why': str(raw.get('why') or '').strip(),
        'evidenceUrls': urls,
        'gateReasons': gate,
        'method': method,
        'taxonomyVersion': load_taxonomy(market)['version'],
        'reviewedAt': datetime.now(KST).isoformat(timespec='seconds'),
        'overrideByUser': False,
        'overrideReason': '',
    }


def _with_history(new: dict, old: dict | None) -> dict:
    if not old:
        new['history'] = []
        return new
    prev = {k: v for k, v in old.items() if k != 'history'}
    new['history'] = ([prev] + list(old.get('history') or []))[:_HISTORY_KEEP]
    return new


def apply_auto(market: str, ticker: str, record: dict, records: dict | None = None) -> bool:
    """자동 분류 결과 반영. 사용자 확정 기록이면 반영하지 않고 False."""
    records = load_store(market) if records is None else records
    old = records.get(ticker)
    if old and old.get('overrideByUser'):
        return False
    records[ticker] = _with_history(record, old)
    return True


def set_override(market: str, ticker: str, primary: str, secondary: list,
                 reason: str, records: dict | None = None) -> dict:
    """사용자 확정 분류. 이후 자동 분류가 덮어쓰지 않는다."""
    themes = theme_index(market)
    primary = primary.strip().upper()
    if primary not in themes:
        raise ValueError(f'테마 목록에 없는 코드: {primary}')
    records = load_store(market) if records is None else records
    old = records.get(ticker) or {}
    new = {
        **{k: v for k, v in old.items() if k != 'history'},
        'primaryTheme': primary,
        'suggestedTheme': None,
        'secondaryThemes': [s for s in secondary if s in themes and s not in (primary, UNCLASSIFIED)],
        'confidence': 'high',
        'gateReasons': [],
        'overrideByUser': True,
        'overrideReason': reason.strip(),
        'method': 'user',
        'taxonomyVersion': load_taxonomy(market)['version'],
        'reviewedAt': datetime.now(KST).isoformat(timespec='seconds'),
    }
    records[ticker] = _with_history(new, old or None)
    return records[ticker]


def missing_tickers(market: str, tickers: list) -> list:
    """분류 기록이 아예 없는 종목 (신규 편입 자동 분류 대상)."""
    records = load_store(market)
    return [t for t in dict.fromkeys(tickers) if t not in records]


def primary_map(market: str, tickers: list) -> dict:
    """{ticker: primaryTheme code} — 기록이 없으면 UNCLASSIFIED."""
    records = load_store(market)
    return {t: (records.get(t) or {}).get('primaryTheme', UNCLASSIFIED) for t in tickers}


def display_label(market: str, ticker: str) -> str | None:
    """와치리스트 테마 컬럼용 라벨. 승인된 분류가 없으면 None (호출부가 구 캐시로 폴백)."""
    rec = load_store(market).get(ticker)
    if not rec or rec.get('primaryTheme', UNCLASSIFIED) == UNCLASSIFIED:
        return None
    if rec.get('detail'):
        return rec['detail']
    return theme_index(market).get(rec['primaryTheme'], {}).get('label')
