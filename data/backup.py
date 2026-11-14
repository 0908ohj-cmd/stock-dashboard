"""티커 백업(JSON) 복원 — 파싱과 '이 내용을 이미 처리했나' 판정. Streamlit 무의존.

st.file_uploader는 rerun마다 같은 파일을 다시 돌려준다. 복원처럼 처리 끝에 st.rerun()을
부르는 경로는 내용 서명 가드가 없으면 '복원 → GitHub 커밋 → rerun → 복원 …'으로 끝없이 돈다(#36).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, MutableMapping


def content_sig(raw: bytes) -> str:
    """내용 기반 서명 — 이름·크기가 같은 다른 파일도 구별한다."""
    return hashlib.md5(raw).hexdigest()


def claim_new_content(state: MutableMapping, slot: str, raw: bytes) -> bool:
    """이 세션에서 아직 처리하지 않은 내용이면 서명을 기록하고 True, 이미 처리했으면 False."""
    sig = content_sig(raw)
    if state.get(slot) == sig:
        return False
    state[slot] = sig
    return True


def release(state: MutableMapping, slot: str) -> None:
    """서명을 지운다 — 업로더를 비웠다가 같은 파일을 다시 올리면 다시 처리되게."""
    state.pop(slot, None)


def parse_backup(raw: bytes, keys: Iterable[str]) -> dict[str, list[str]]:
    """{시장 키: [티커]} — 아는 시장 키의 목록 값만 남긴다. 복원할 시장이 없으면 ValueError."""
    data = json.loads(raw.decode('utf-8'))     # JSONDecodeError·UnicodeDecodeError 모두 ValueError
    if not isinstance(data, dict):
        raise ValueError('백업 파일 형식이 아닙니다 (JSON 객체가 아님)')
    known = set(keys)
    out = {
        key: [t.strip() for t in tickers if isinstance(t, str) and t.strip()]
        for key, tickers in data.items()
        if key in known and isinstance(tickers, list)
    }
    if not out:
        raise ValueError(f"복원할 시장이 없습니다 (필요한 키: {', '.join(sorted(known))})")
    return out


def changed_markets(restored: dict[str, list[str]], current: dict[str, str]) -> dict[str, str]:
    """{시장 키: 저장할 파일 내용} — 지금 저장된 목록(current: 파일 내용)과 다른 시장만.

    같은 목록을 다시 쓰면 GitHub 커밋과 시세 재수집만 늘어난다."""
    out = {}
    for key, tickers in restored.items():
        saved = [t.strip() for t in (current.get(key) or '').splitlines() if t.strip()]
        if saved != tickers:
            out[key] = '\n'.join(tickers)
    return out
