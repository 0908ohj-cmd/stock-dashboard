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


# ── apply_upload (#44 M15) ────────────────────────────────
# 사이드바 티커 업로드도 file_uploader라 rerun마다 같은 파일이 돌아온다. 저장·GitHub 커밋이
# 서명 가드 밖에 있어서 위젯을 만질 때마다 파일을 다시 쓰고 PUT했다(빈 커밋 279개, 파일 mtime이
# 바뀌어 주도섹터 캐시도 매번 무효화).

class _Persist:
    """_github_save 대역 — (파일명, 내용) 호출 기록."""

    def __init__(self):
        self.calls = []

    def __call__(self, name, content):
        self.calls.append((name, content))


def test_upload_saves_once_across_reruns(tmp_path):
    """같은 업로드가 rerun마다 돌아와도 파일 쓰기·GitHub 저장은 처음 한 번뿐이다."""
    state, persist = {}, _Persist()
    path = tmp_path / 'kospi.tickers'
    raw = b'KRX:005930,KRX:000660'

    results = [backup.apply_upload(state, 'up', raw, ['005930', '000660'], path, persist) for _ in range(5)]

    assert results == [backup.UPLOAD_SAVED] + [backup.UPLOAD_REPEAT] * 4
    assert path.read_text(encoding='utf-8') == '005930\n000660'
    assert persist.calls == [('kospi.tickers', '005930\n000660')]


def test_upload_does_not_touch_file_on_rerun(tmp_path):
    """rerun에 파일을 다시 쓰지 않는다 — mtime이 바뀌면 주도섹터 _compute가 다시 돈다."""
    state, persist = {}, _Persist()
    path = tmp_path / 'kospi.tickers'
    raw = b'KRX:005930'
    backup.apply_upload(state, 'up', raw, ['005930'], path, persist)
    mtime = path.stat().st_mtime_ns

    backup.apply_upload(state, 'up', raw, ['005930'], path, persist)

    assert path.stat().st_mtime_ns == mtime


def test_upload_same_as_saved_file_skips_write_and_persist(tmp_path):
    """새 업로드라도 저장된 목록과 같으면 쓰지도 PUT하지도 않는다(빈 커밋 방지). 새 업로드라는 사실은 알린다."""
    state, persist = {}, _Persist()
    path = tmp_path / 'us.tickers'
    path.write_text('NVDA\nAMZN\n', encoding='utf-8')
    mtime = path.stat().st_mtime_ns

    result = backup.apply_upload(state, 'up', b'NVDA,AMZN', ['NVDA', 'AMZN'], path, persist)

    assert result == backup.UPLOAD_UNCHANGED
    assert persist.calls == []
    assert path.stat().st_mtime_ns == mtime


def test_upload_with_different_content_saves_again(tmp_path):
    state, persist = {}, _Persist()
    path = tmp_path / 'us.tickers'
    backup.apply_upload(state, 'up', b'NVDA', ['NVDA'], path, persist)

    result = backup.apply_upload(state, 'up', b'NVDA,AMZN', ['NVDA', 'AMZN'], path, persist)

    assert result == backup.UPLOAD_SAVED
    assert path.read_text(encoding='utf-8') == 'NVDA\nAMZN'
    assert [c[1] for c in persist.calls] == ['NVDA', 'NVDA\nAMZN']


def test_upload_reprocessed_after_release(tmp_path):
    """업로더를 비웠다가 같은 파일을 다시 올리면 새 업로드로 처리한다(저장본과 같으니 쓰기는 없음)."""
    state, persist = {}, _Persist()
    path = tmp_path / 'us.tickers'
    backup.apply_upload(state, 'up', b'NVDA', ['NVDA'], path, persist)
    backup.release(state, 'up')

    assert backup.apply_upload(state, 'up', b'NVDA', ['NVDA'], path, persist) == backup.UPLOAD_UNCHANGED
    assert len(persist.calls) == 1


def test_upload_write_failure_is_retried_on_next_rerun(tmp_path):
    """파일 쓰기가 실패하면 서명을 남기지 않는다 — 다음 rerun에서 다시 시도한다(전처럼 오류가 다시 보인다)."""
    state, persist = {}, _Persist()
    path = tmp_path / 'missing_dir' / 'us.tickers'      # 상위 폴더가 없어 쓰기 실패

    with pytest.raises(OSError):
        backup.apply_upload(state, 'up', b'NVDA', ['NVDA'], path, persist)
    assert persist.calls == []

    path.parent.mkdir()
    assert backup.apply_upload(state, 'up', b'NVDA', ['NVDA'], path, persist) == backup.UPLOAD_SAVED
