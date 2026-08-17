"""
통합 섹터 분류기 — stockEdge portfolio_advisor/sector_classifier.py 이식본
(캐시 경로 상수만 다름. 개선 시 원본에서 다시 복사할 것.)

- 캐시 우선 (TTL 7일)
- 1차: 종목별 LLM 분류 ({theme, detail}) — claude haiku WebSearch
- 2차: '기타' 묶음 resolve → known_themes 자동 확장
"""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

_CACHE_FILE = Path(__file__).parent.parent / "theme_cache.json"
_THEMES_FILE = Path(__file__).parent / "themes.json"
_CACHE_TTL_DAYS = 7
# claude 바이너리 위치는 환경마다 다를 수 있어 동적으로 해석한다
# (CLAUDE_BIN 환경변수 → PATH 검색 → ~/.local/bin fallback)
_CLAUDE_BIN = (
    os.environ.get("CLAUDE_BIN")
    or shutil.which("claude")
    or os.path.expanduser("~/.local/bin/claude")
)
_MAX_RESOLVE_BATCH = 20


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _is_fresh(entry: dict) -> bool:
    if "detail" not in entry:
        return False  # 구 포맷 → 미스
    try:
        updated = datetime.fromisoformat(entry["updated"])
        return datetime.now() - updated < timedelta(days=_CACHE_TTL_DAYS)
    except Exception:
        return False


_FIRST_SYSTEM_PROMPT = (
    "당신은 주식 분류 전문가입니다. "
    "주어진 종목 ticker를 웹 검색으로 확인하여 현재 핵심 사업을 파악한 뒤, "
    "대분류와 상세 테마를 JSON 한 줄로만 출력하세요. "
    "다른 텍스트는 절대 출력 금지."
)


def _build_first_prompt(ticker: str, name_hint: str | None, themes: list[dict]) -> str:
    themes_block = "\n".join(f"- {t['name']}: {t['description']}" for t in themes)
    name_part = f" ({name_hint})" if name_hint else ""
    return (
        f"종목: {ticker}{name_part}\n"
        f"※ 6자리 숫자 코드는 KRX(한국) 종목, 그 외는 미국 종목입니다.\n\n"
        f"먼저 웹 검색으로 이 종목의 **최근 1-2년(2025-2026) 시장에서 주목받는 핵심 테마/사업**을 파악한 뒤,\n"
        f"아래 대분류 중 가장 가까운 것 하나를 선택하고,\n"
        f"detail은 최근 시장이 이 종목을 부르는 2~4단어의 한국어 테마 표현으로 작성하라.\n\n"
        f"분류 원칙:\n"
        f"① description의 '예:' 뒤에 명시된 종목은 반드시 그 대분류로 매칭하라 (예외 없음).\n"
        f"② 한 회사가 여러 사업을 한다면 **시장이 가장 주목하는 최신 테마**를 우선하라.\n"
        f"   예: LG이노텍은 카메라모듈도 하지만 최근은 'AI 반도체 기판'이 시장 테마, "
        f"SK하이닉스는 DRAM/NAND/HBM 다 하지만 핵심은 'HBM·AI 메모리'.\n"
        f"③ 단, 'AI 솔루션', '클라우드 스토리지' 같은 모호한 마케팅 슬로건이 아니라 "
        f"실제 시장에서 통용되는 구체적 테마(예: '데이터센터 광부품', 'HBM 메모리', '자율드론 방어')를 써라.\n"
        f"④ 대분류가 정말 어느 것에도 안 맞으면 'theme'을 \"기타\"로 두되, "
        f"detail은 비워두지 말고 최근 시장 테마를 2~4단어로 표현하라.\n\n"
        f"{themes_block}\n\n"
        f'출력 형식 (반드시 이 JSON 한 줄, 다른 텍스트 금지):\n'
        f'{{"theme": "<위 목록 중 정확히 하나, 안 맞으면 \\"기타\\">", "detail": "<항상 2~4단어 한국어, 절대 비우지 말 것>"}}'
    )


def _classify_one(ticker: str, name_hint: str | None, themes: list[dict]) -> dict | None:
    """1차 분류. 성공 시 {theme, detail}, '기타' 마킹 시 None."""
    theme_names = {t["name"] for t in themes}
    prompt = _build_first_prompt(ticker, name_hint, themes)
    try:
        proc = subprocess.run(
            [_CLAUDE_BIN, "-p", prompt,
             "--system-prompt", _FIRST_SYSTEM_PROMPT,
             "--model", "claude-haiku-4-5-20251001",
             "--allowedTools", "WebSearch",
             "--permission-mode", "acceptEdits",
             "--output-format", "text"],
            capture_output=True, text=True, timeout=90,
        )
        text = proc.stdout.strip()
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(0))
        theme = data.get("theme", "").strip()
        detail = data.get("detail", "").strip()
        if theme in theme_names and theme != "기타" and detail:
            return {"theme": theme, "detail": detail}
        # 검증 실패해도 detail이 있으면 임시 '기타' + detail 보존 (2차 resolve 단계로)
        if detail:
            return {"theme": "기타", "detail": detail}
        return None
    except Exception:
        return None


def classify(
    tickers: list[str],
    themes: list[dict],
    overrides: dict[str, str] | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, dict]:
    cache = _load_cache()
    overrides = overrides or {}
    names = names or {}
    result: dict[str, dict] = {}
    cache_dirty = False

    for ticker in tickers:
        if ticker in overrides:
            ovr = overrides[ticker]
            result[ticker] = {"theme": ovr, "detail": ovr}
            continue

        entry = cache.get(ticker, {})
        if _is_fresh(entry):
            result[ticker] = {"theme": entry["theme"], "detail": entry["detail"]}
            continue

        # 캐시 미스/만료 → 1차 분류 (일시적 실패 대비 1회 재시도)
        first = _classify_one(ticker, names.get(ticker), themes)
        if first is None:
            first = _classify_one(ticker, names.get(ticker), themes)
        if first is not None:
            cache[ticker] = {
                **first,
                "updated": datetime.now().isoformat(),
            }
            result[ticker] = first
            cache_dirty = True
        else:
            # 1차 완전 실패(detail 확보 실패) — 티커 철자만으로 추측 금지.
            # '기타'로 두되 캐시 저장 보류 → 다음 회차 재시도(자가치유).
            result[ticker] = {"theme": "기타", "detail": ""}

    # 2차 resolve — detail 후보가 있는 '기타'만 대상.
    # detail이 비어 있으면(=1차 완전 실패) 2차는 티커 철자만 보고 엉뚱한 대분류를
    # 지어내므로(예: FROG→헬스케어) resolve 대상에서 제외한다.
    misc_tickers = [
        t for t, r in result.items()
        if r["theme"] == "기타" and r.get("detail") and t not in overrides
    ]
    if misc_tickers:
        resolved = _resolve_misc(misc_tickers, result, themes)
        now = datetime.now().isoformat()
        for ticker in misc_tickers:
            if ticker in resolved:
                result[ticker] = resolved[ticker]
                cache[ticker] = {**resolved[ticker], "updated": now}
            else:
                # 2차 실패: '기타'로 캐시 (자가치유)
                cache[ticker] = {"theme": "기타", "detail": result[ticker].get("detail", ""), "updated": now}
            cache_dirty = True

    if cache_dirty:
        _save_cache(cache)

    return result


_RESOLVE_SYSTEM_PROMPT = (
    "당신은 주식 분류 전문가입니다. "
    "주어진 종목들을 묶을 새 대분류 이름과 description을 JSON 배열로만 출력하세요. "
    "다른 텍스트는 절대 출력 금지."
)


def _build_resolve_prompt(tickers: list[str], current_result: dict) -> str:
    lines = [f"- {t} (detail 후보: \"{current_result[t].get('detail', '')}\")" for t in tickers]
    return (
        "다음 종목들이 기존 known_themes 어느 것에도 잘 맞지 않는다.\n"
        "각 종목에 어울리는 새 대분류 이름(2~6단어 한국어)과 한 문장 description을 제안하라.\n"
        "비슷한 종목은 같은 대분류로 묶어라.\n\n"
        + "\n".join(lines)
        + "\n\n출력 (반드시 JSON 배열, 다른 텍스트 금지):\n"
        '[{"theme": "<신규 대분류>", "description": "<한 문장>", "members": ["<ticker>", ...]}]'
    )


def _normalize_theme_name(name: str) -> str:
    return re.sub(r"\s+", "", name).lower()


def _append_known_themes(new_entries: list[dict]) -> None:
    """themes.json:known_themes에 신규 항목 atomic append."""
    data = json.loads(_THEMES_FILE.read_text(encoding="utf-8"))
    existing = {_normalize_theme_name(t["name"]) for t in data["known_themes"]}
    added = False
    for entry in new_entries:
        key = _normalize_theme_name(entry["name"])
        if key in existing:
            continue
        data["known_themes"].append({"name": entry["name"], "description": entry["description"]})
        existing.add(key)
        added = True
    if added:
        tmp = _THEMES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, _THEMES_FILE)


def _resolve_misc(tickers: list[str], current_result: dict, themes: list[dict]) -> dict[str, dict]:
    if not tickers:
        return {}
    batch = tickers[:_MAX_RESOLVE_BATCH]
    prompt = _build_resolve_prompt(batch, current_result)
    try:
        proc = subprocess.run(
            [_CLAUDE_BIN, "-p", prompt, "--system-prompt", _RESOLVE_SYSTEM_PROMPT,
             "--model", "claude-haiku-4-5-20251001", "--output-format", "text"],
            capture_output=True, text=True, timeout=60,
        )
        text = proc.stdout.strip()
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return {}
        groups = json.loads(m.group(0))
    except Exception:
        return {}

    existing_names = {t["name"] for t in themes}
    existing_norm = {_normalize_theme_name(n): n for n in existing_names}

    new_to_append: list[dict] = []
    resolved: dict[str, dict] = {}
    for g in groups:
        theme = (g.get("theme") or "").strip()
        description = (g.get("description") or "").strip()
        members = g.get("members") or []
        if not theme or not members:
            continue
        # 1종목짜리 그룹은 신규 대분류로 인정 안 함 — 자연 적재 위해 '기타' 유지
        if len(members) < 2:
            continue
        norm = _normalize_theme_name(theme)
        canonical = existing_norm.get(norm, theme)
        if norm not in existing_norm and canonical != "기타":
            new_to_append.append({"name": canonical, "description": description})
            existing_norm[norm] = canonical
        for ticker in members:
            if ticker in batch:
                detail = current_result.get(ticker, {}).get("detail") or canonical
                resolved[ticker] = {"theme": canonical, "detail": detail}

    if new_to_append:
        _append_known_themes(new_to_append)

    return resolved
