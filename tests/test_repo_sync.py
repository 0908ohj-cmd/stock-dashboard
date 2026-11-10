import json
import os
import subprocess
from datetime import datetime, time as dtime, timedelta, timezone

import pytest

from data import repo_sync as rs

KST = timezone(timedelta(hours=9))
# 2026-11: 2=월 3=화 4=수 5=목 6=금 7=토 8=일

ENV = {
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
}

IDX = 'data/ohlcv/indices.json'
KOSPI = 'data/ohlcv/KR_KOSPI.json'
TICKERS = 'data/saved/kospi.tickers'


def _snap(fetched_at, close):
    return json.dumps({'market': 'indices', 'fetched_at': fetched_at,
                       'last_trading_date': fetched_at[:10], 'data': {'KOSPI': {'close': [close]}}})


def _git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _write(cwd, rel, content):
    p = cwd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')


def _push(batch, files: dict, msg='batch'):
    """맥 배치 흉내 — 파일을 커밋해 원격에 푸시."""
    for rel, content in files.items():
        _write(batch, rel, content)
        _git(batch, 'add', rel)
    _git(batch, 'commit', '-q', '-m', msg)
    _git(batch, 'push', '-q', 'origin', 'HEAD:main')


def _read(cwd, rel):
    return (cwd / rel).read_text(encoding='utf-8')


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """원격(bare) + 배치 체크아웃 + Cloud 체크아웃."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(origin))
    batch = tmp_path / 'batch'
    _git(tmp_path, 'clone', '-q', str(origin), str(batch))
    _git(batch, 'checkout', '-q', '-b', 'main')
    _push(batch, {
        IDX: _snap('2026-11-04T17:34:00+09:00', 100),
        KOSPI: _snap('2026-11-04T17:34:00+09:00', 1),
        TICKERS: 'A\n',
    }, msg='init')
    cloud = tmp_path / 'cloud'
    _git(tmp_path, 'clone', '-q', str(origin), str(cloud))
    return batch, cloud


# ── 동기화 ────────────────────────────────────────────────

def test_clean_worktree_fast_forwards(repos):
    batch, cloud = repos
    _push(batch, {IDX: _snap('2026-11-05T17:34:00+09:00', 200)})

    r = rs.sync_from_origin(cloud)

    assert r.ok and r.updated
    assert json.loads(_read(cloud, IDX))['data']['KOSPI']['close'] == [200]
    assert _git(cloud, 'rev-parse', 'HEAD') == _git(batch, 'rev-parse', 'HEAD')


def test_no_new_commits_is_ok_without_update(repos):
    _, cloud = repos
    r = rs.sync_from_origin(cloud)
    assert r.ok and not r.updated and r.error == ''


def test_stale_local_snapshot_is_replaced_by_newer_batch(repos):
    """재현된 버그: Cloud가 스냅샷을 고친 뒤 배치가 같은 파일을 푸시하면 pull이 막혔다."""
    batch, cloud = repos
    _write(cloud, IDX, _snap('2026-11-05T10:00:00+09:00', 150))      # 장중 재수집
    _push(batch, {IDX: _snap('2026-11-05T17:34:00+09:00', 200)})      # 마감 후 배치

    r = rs.sync_from_origin(cloud)

    assert r.ok and r.updated
    assert r.replaced_local == [IDX] and r.kept_local == []
    assert json.loads(_read(cloud, IDX))['data']['KOSPI']['close'] == [200]


def test_newer_local_snapshot_survives_and_other_files_still_update(repos):
    """배치가 빠진 날 손으로 재수집한 최신 값을 옛 배치 값으로 되돌리면 안 된다."""
    batch, cloud = repos
    _write(cloud, IDX, _snap('2026-11-05T21:00:00+09:00', 300))      # 배치 이후 수동 재수집
    _push(batch, {IDX: _snap('2026-11-05T17:34:00+09:00', 200),
                  KOSPI: _snap('2026-11-05T17:34:00+09:00', 2)})

    r = rs.sync_from_origin(cloud)

    assert r.ok and r.updated
    assert r.kept_local == [IDX] and r.replaced_local == []
    assert json.loads(_read(cloud, IDX))['data']['KOSPI']['close'] == [300]
    assert json.loads(_read(cloud, KOSPI))['data']['KOSPI']['close'] == [2]
    assert _git(cloud, 'rev-parse', 'HEAD') == _git(batch, 'rev-parse', 'HEAD')


def test_conflicting_non_snapshot_file_takes_remote(repos):
    batch, cloud = repos
    _write(cloud, TICKERS, 'LOCAL\n')
    _push(batch, {TICKERS: 'REMOTE\n'})

    r = rs.sync_from_origin(cloud)

    assert r.ok and r.replaced_local == [TICKERS]
    assert _read(cloud, TICKERS) == 'REMOTE\n'


def test_dirty_file_untouched_upstream_is_left_alone(repos):
    batch, cloud = repos
    _write(cloud, KOSPI, _snap('2026-11-05T10:00:00+09:00', 9))      # 누락 종목 보충 등
    _push(batch, {IDX: _snap('2026-11-05T17:34:00+09:00', 200)})

    r = rs.sync_from_origin(cloud)

    assert r.ok and r.updated and r.replaced_local == [] and r.kept_local == []
    assert json.loads(_read(cloud, KOSPI))['data']['KOSPI']['close'] == [9]


def test_untracked_file_added_upstream_takes_remote(repos):
    batch, cloud = repos
    new = 'data/ohlcv/US.json'
    _write(cloud, new, 'not json')
    _push(batch, {new: _snap('2026-11-05T08:13:00+09:00', 5)})

    r = rs.sync_from_origin(cloud)

    assert r.ok and r.replaced_local == [new]
    assert json.loads(_read(cloud, new))['data']['KOSPI']['close'] == [5]


def test_fetch_failure_is_reported(repos):
    _, cloud = repos
    _git(cloud, 'remote', 'set-url', 'origin', str(cloud.parent / 'nowhere.git'))

    r = rs.sync_from_origin(cloud)

    assert not r.ok and not r.updated
    assert 'fetch' in r.error


def test_origin_이_막혀도_주소를_직접_주면_받아온다(repos):
    # Streamlit Cloud: origin(배포 인증 주소) fetch가 권한 오류 → 공개 주소로 직접 fetch
    batch, cloud = repos
    real = cloud.parent / 'origin.git'
    _git(cloud, 'remote', 'set-url', 'origin', str(cloud.parent / 'nowhere.git'))
    _push(batch, {IDX: _snap('2026-11-05T17:34:00+09:00', 200)})

    assert not rs.sync_from_origin(cloud).ok                 # origin으로는 실패
    r = rs.sync_from_origin(cloud, remote=str(real))

    assert r.ok and r.updated
    assert json.loads(_read(cloud, IDX))['data']['KOSPI']['close'] == [200]


def test_merge_failure_restores_local_edits(repos):
    """fast-forward가 안 되면(이력 갈라짐) 실패를 알리고 로컬 수정은 그대로 둔다."""
    batch, cloud = repos
    _write(cloud, 'other.txt', 'x')
    _git(cloud, 'add', 'other.txt')
    _git(cloud, 'commit', '-q', '-m', 'diverge')
    local = _snap('2026-11-05T10:00:00+09:00', 150)
    _write(cloud, IDX, local)
    _push(batch, {IDX: _snap('2026-11-05T17:34:00+09:00', 200)})

    r = rs.sync_from_origin(cloud)

    assert not r.ok and 'merge' in r.error
    assert _read(cloud, IDX) == local


def test_not_a_repo_does_not_raise(tmp_path):
    r = rs.sync_from_origin(tmp_path)
    assert not r.ok and r.error


# ── 슬롯 판정 ────────────────────────────────────────────

def _at(day, h, m):
    return datetime(2026, 11, day, h, m, tzinfo=KST)


def test_pull_due_before_first_slot_is_false():
    assert rs.pull_due(_at(5, 8, 0), None) is False


def test_pull_due_once_per_slot():
    assert rs.pull_due(_at(5, 8, 31), None) is True
    assert rs.pull_due(_at(5, 9, 0), _at(5, 8, 31)) is False
    assert rs.pull_due(_at(5, 11, 31), _at(5, 8, 31)) is True
    assert rs.pull_due(_at(5, 17, 49), _at(5, 11, 31)) is False
    assert rs.pull_due(_at(5, 17, 50), _at(5, 11, 31)) is True
    assert rs.pull_due(_at(5, 20, 31), _at(5, 17, 50)) is True
    assert rs.pull_due(_at(5, 23, 0), _at(5, 20, 31)) is False


def test_pull_due_catches_up_after_process_restart():
    """프로세스가 저녁에 새로 떠도(last=None) 한 번은 당긴다."""
    assert rs.pull_due(_at(5, 22, 0), None) is True


def test_pull_due_skips_weekend():
    assert rs.pull_due(_at(7, 9, 0), None) is False
    assert rs.pull_due(_at(8, 21, 0), None) is False


def test_kr_main_slot_follows_kr_batch():
    """본 슬롯은 각 로컬 배치 시작 후 30분 안에 있어야 한다 — 배치보다 앞서면 헛돌고
    (15:46/07:01 슬롯이 그랬다) 폴백(20:30)만 남는다."""
    from scripts.check_snapshot_fresh import LOCAL_SLOTS

    def minutes(h, m):
        return h * 60 + m

    slots = [minutes(s.hour, s.minute) for s in rs.PULL_SCHEDULES]
    for market in ('kr', 'us'):
        start = minutes(LOCAL_SLOTS[market]['hour'], LOCAL_SLOTS[market]['minute'])
        assert any(0 < s - start <= 30 for s in slots), market


# ── maybe_sync: 상태가 rerun·세션을 넘어 유지된다 ─────────

@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.setattr(rs, '_last_attempt', None)
    monkeypatch.setattr(rs, '_last_result', None)


def test_maybe_sync_runs_once_per_slot(fresh_state, tmp_path):
    calls = []

    def fake(root):
        calls.append(root)
        return rs.SyncResult(at=_at(5, 8, 31), ok=True, updated=True)

    assert rs.maybe_sync(tmp_path, now=_at(5, 8, 31), sync_fn=fake).updated
    assert rs.maybe_sync(tmp_path, now=_at(5, 8, 40), sync_fn=fake) is None   # 같은 슬롯 rerun
    assert rs.maybe_sync(tmp_path, now=_at(5, 11, 31), sync_fn=fake) is not None
    assert len(calls) == 2


def test_maybe_sync_keeps_last_failure_for_warning(fresh_state, tmp_path):
    def fail(root):
        return rs.SyncResult(at=_at(5, 8, 31), ok=False, error='git fetch 실패: x')

    rs.maybe_sync(tmp_path, now=_at(5, 8, 31), sync_fn=fail)
    assert rs.maybe_sync(tmp_path, now=_at(5, 9, 0), sync_fn=fail) is None
    assert rs.last_result().ok is False and 'fetch' in rs.last_result().error


def test_maybe_sync_skips_while_another_session_syncs(fresh_state, tmp_path):
    rs._lock.acquire()
    try:
        assert rs.maybe_sync(tmp_path, now=_at(5, 8, 31),
                             sync_fn=lambda r: pytest.fail('동시 실행됨')) is None
    finally:
        rs._lock.release()
