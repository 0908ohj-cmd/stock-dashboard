"""티커 백업 복원 — 파싱과 같은 파일 재처리 방지 가드 (#36).

file_uploader는 rerun마다 같은 파일을 돌려준다. 복원 끝의 st.rerun()이 가드 없이
돌면 '복원 → GitHub 커밋 ×3 → rerun → 복원 …' 무한 루프가 된다.
"""
import json

import pytest

from data import backup

KEYS = ('KR_KOSPI', 'KR_KOSDAQ', 'US')


def _raw(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


# ── claim_new_content ─────────────────────────────────────

def test_first_upload_is_claimed():
    state = {}
    assert backup.claim_new_content(state, 'sig', b'{"US": ["NVDA"]}') is True


def test_same_content_on_rerun_is_not_claimed_again():
    """rerun마다 같은 파일이 돌아와도 두 번째부터는 처리하지 않는다 — 무한 루프 차단."""
    state = {}
    raw = b'{"US": ["NVDA"]}'
    backup.claim_new_content(state, 'sig', raw)

    assert [backup.claim_new_content(state, 'sig', raw) for _ in range(5)] == [False] * 5


def test_different_content_is_claimed():
    """같은 이름·길이라도 내용이 다르면 새 복원이다 (내용 해시 기준)."""
    state = {}
    backup.claim_new_content(state, 'sig', b'{"US": ["NVDA"]}')

    assert backup.claim_new_content(state, 'sig', b'{"US": ["AMZN"]}') is True


def test_slots_are_independent():
    state = {}
    raw = b'{"US": ["NVDA"]}'
    backup.claim_new_content(state, 'a', raw)

    assert backup.claim_new_content(state, 'b', raw) is True


def test_released_slot_claims_same_content_again():
    """업로더를 비웠다가 같은 파일을 다시 올리면 다시 복원돼야 한다."""
    state = {}
    raw = b'{"US": ["NVDA"]}'
    backup.claim_new_content(state, 'sig', raw)
    backup.release(state, 'sig')

    assert backup.claim_new_content(state, 'sig', raw) is True


def test_release_of_unclaimed_slot_is_noop():
    state = {}
    backup.release(state, 'sig')
    assert state == {}


# ── parse_backup ──────────────────────────────────────────

def test_parse_keeps_known_markets_only():
    raw = _raw({'KR_KOSPI': ['005930'], 'US': ['NVDA', 'AMZN'], 'JP': ['7203']})

    assert backup.parse_backup(raw, KEYS) == {'KR_KOSPI': ['005930'], 'US': ['NVDA', 'AMZN']}


def test_parse_drops_blank_and_non_string_entries():
    raw = _raw({'US': ['NVDA', '', '  ', None, 3, ' AMZN ']})

    assert backup.parse_backup(raw, KEYS) == {'US': ['NVDA', 'AMZN']}


def test_parse_keeps_empty_market_list():
    """다운로드한 백업엔 티커가 없는 시장도 빈 목록으로 들어 있다 — 복원 시 그대로 비운다(기존 동작)."""
    raw = _raw({'KR_KOSPI': [], 'US': ['NVDA']})

    assert backup.parse_backup(raw, KEYS) == {'KR_KOSPI': [], 'US': ['NVDA']}


def test_parse_ignores_non_list_values():
    raw = _raw({'KR_KOSPI': '005930', 'US': ['NVDA']})

    assert backup.parse_backup(raw, KEYS) == {'US': ['NVDA']}


def test_parse_rejects_non_object_json():
    with pytest.raises(ValueError):
        backup.parse_backup(_raw(['NVDA']), KEYS)


def test_parse_rejects_backup_without_known_market():
    """복원할 게 없는데 '완료'로 처리하면 안 된다."""
    with pytest.raises(ValueError):
        backup.parse_backup(_raw({'JP': ['7203']}), KEYS)


def test_parse_rejects_invalid_json():
    with pytest.raises(ValueError):
        backup.parse_backup(b'not json', KEYS)


# ── changed_markets ───────────────────────────────────────

def test_changed_markets_skips_market_with_same_tickers():
    """같은 목록을 다시 쓰고 재수집하면 GitHub 커밋·시세 수집만 늘어난다."""
    restored = {'KR_KOSPI': ['005930'], 'US': ['NVDA', 'AMZN']}
    current = {'KR_KOSPI': '005930', 'US': 'NVDA'}

    assert backup.changed_markets(restored, current) == {'US': 'NVDA\nAMZN'}


def test_changed_markets_includes_market_without_saved_file():
    assert backup.changed_markets({'US': ['NVDA']}, {}) == {'US': 'NVDA'}


def test_changed_markets_ignores_blank_lines_in_saved_file():
    """저장 파일 끝 개행·빈 줄은 목록 차이가 아니다."""
    assert backup.changed_markets({'US': ['NVDA', 'AMZN']}, {'US': 'NVDA\n\nAMZN\n'}) == {}


def test_changed_markets_includes_emptied_market():
    """백업의 빈 목록은 그 시장을 비우는 것 — 기존 동작대로 파일을 비운다."""
    assert backup.changed_markets({'KR_KOSPI': []}, {'KR_KOSPI': '005930'}) == {'KR_KOSPI': ''}
