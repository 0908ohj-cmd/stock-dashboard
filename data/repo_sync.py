"""실행 중인 Cloud 앱에 맥 배치가 푸시한 데이터를 반영한다 (GitHub → 앱 작업본).

Streamlit 무의존 — 단위 테스트를 위해 상태와 로직을 이 모듈에 둔다.

왜 app.py가 아니라 여기인가:
  Streamlit은 rerun마다 app.py를 새 네임스페이스로 다시 실행하므로 app.py 전역은
  rerun 사이에 유지되지 않는다(2026-11 AppTest로 확인 — 전역 카운터가 매 실행 1).
  예전 `_last_auto_pull`이 매번 0으로 초기화돼, 첫 슬롯 이후 모든 rerun마다 git pull이
  돌았다. import된 모듈 전역은 프로세스 동안 유지되므로 상태를 여기에 둔다.

왜 `git pull`이 아닌가:
  Cloud 앱은 업로드·🔄 재수집·누락 종목 보충 시 git 추적 파일(data/ohlcv/*.json 등)을
  직접 고친다. 그 상태에서 배치가 같은 파일을 푸시하면 `git pull`은 "local changes would be
  overwritten"으로 중단되고, 컨테이너가 재시작될 때까지 배치 데이터가 영영 반영되지 않는다.
  예전 코드는 stdout의 "Updating ..."만 보고 성공으로 오판해 캐시만 비웠다.
  그래서 충돌 파일만 골라 승자를 정한 뒤 fast-forward 한다:
    - 스냅샷(data/ohlcv/*.json): fetched_at이 더 최근인 쪽 — 배치가 빠진 날 손으로 재수집한
      최신 값을 옛 배치 값으로 되돌리지 않기 위해서
    - 그 외 파일: 원격 — 티커 업로드는 _github_save로 원격에도 올라가고, 캐시는 재생성 가능
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Callable

KST = timezone(timedelta(hours=9))

# 맥 launchd 배치 푸시 직후 시각 (KST) — scripts/launchd/*.plist와 맞출 것.
#   US 화~토 08:10 시작(≈08:13 푸시) → 08:30, 폴백 11:30
#   KR 월~금 17:30 시작(≈17:34 푸시) → 17:50, 폴백 20:30
# 폴백 슬롯은 배치가 지연·재시도돼도 같은 날 안에 따라잡기 위한 것이다.
PULL_SCHEDULES: tuple[dtime, ...] = (dtime(8, 30), dtime(11, 30), dtime(17, 50), dtime(20, 30))

SNAPSHOT_PREFIX = 'data/ohlcv/'
GIT_TIMEOUT_SEC = 30


@dataclass
class SyncResult:
    at: datetime
    ok: bool
    updated: bool = False                                   # 새 커밋을 작업본에 반영했는가
    kept_local: list[str] = field(default_factory=list)     # 로컬이 더 최신이라 유지한 파일
    replaced_local: list[str] = field(default_factory=list)  # 원격으로 덮어쓴 로컬 수정
    error: str = ''


# 프로세스 단위 상태 — 모든 세션·rerun이 공유한다
_lock = threading.Lock()
_last_attempt: datetime | None = None
_last_result: SyncResult | None = None


def pull_due(now: datetime, last_attempt: datetime | None,
             schedules: tuple[dtime, ...] = PULL_SCHEDULES) -> bool:
    """오늘 지난 슬롯 중 마지막 시도 이후에 온 것이 있으면 True. 주말은 쉰다."""
    if now.weekday() >= 5:
        return False
    for t in schedules:
        slot = datetime.combine(now.date(), t, tzinfo=KST)
        if now >= slot and (last_attempt is None or last_attempt < slot):
            return True
    return False


def last_result() -> SyncResult | None:
    return _last_result


def maybe_sync(repo_root: pathlib.Path, now: datetime | None = None,
               sync_fn: Callable[[pathlib.Path], SyncResult] | None = None) -> SyncResult | None:
    """슬롯이 됐으면 1회 동기화하고 결과를 반환, 아니면 None.

    여러 세션이 동시에 들어와도 git은 한 번만 돈다 — 진행 중이면 나머지는 건너뛴다.
    """
    global _last_attempt, _last_result
    now = now or datetime.now(KST)
    if not pull_due(now, _last_attempt):
        return None
    if not _lock.acquire(blocking=False):
        return None
    try:
        if not pull_due(now, _last_attempt):   # 락 대기 중 다른 세션이 끝냈을 수 있다
            return None
        _last_attempt = now
        _last_result = (sync_fn or sync_from_origin)(repo_root)
        return _last_result
    finally:
        _lock.release()


# ── git 동기화 ────────────────────────────────────────────

class _GitError(Exception):
    pass


def _git(repo_root: pathlib.Path, *args: str) -> str:
    r = subprocess.run(['git', *args], cwd=repo_root, capture_output=True,
                       text=True, timeout=GIT_TIMEOUT_SEC)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise _GitError(f"git {args[0]} 실패: {msg[-1] if msg else f'exit {r.returncode}'}")
    return r.stdout


def _dirty_paths(repo_root: pathlib.Path) -> dict[str, bool]:
    """작업본에서 바뀐 경로 → 추적 파일 여부(False = untracked)."""
    out = _git(repo_root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    entries = out.split('\0')
    result: dict[str, bool] = {}
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        code, path = e[:2], e[3:]
        result[path] = code != '??'
        if code[0] in 'RC':   # rename/copy는 원래 경로가 다음 항목으로 따라온다
            i += 1
    return result


def _fetched_at(raw: bytes | None) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(json.loads(raw.decode('utf-8'))['fetched_at'])
    except Exception:
        return None


def _is_snapshot(path: str) -> bool:
    return path.startswith(SNAPSHOT_PREFIX) and path.endswith('.json')


def _local_wins(local: bytes | None, remote: bytes | None) -> bool:
    lt, rt = _fetched_at(local), _fetched_at(remote)
    return lt is not None and rt is not None and lt > rt


def _remote_bytes(repo_root: pathlib.Path, target: str, path: str) -> bytes | None:
    try:
        return _git(repo_root, 'show', f'{target}:{path}').encode('utf-8')
    except _GitError:
        return None   # 원격에서 삭제된 파일


def sync_from_origin(repo_root: pathlib.Path, branch: str = 'main') -> SyncResult:
    now = datetime.now(KST)
    try:
        _git(repo_root, 'fetch', '--quiet', 'origin', branch)
        head = _git(repo_root, 'rev-parse', 'HEAD').strip()
        target = _git(repo_root, 'rev-parse', 'FETCH_HEAD').strip()
        if head == target:
            return SyncResult(at=now, ok=True)

        changed = set(_git(repo_root, 'diff', '--name-only', '--no-renames', '-z',
                           head, target).split('\0')) - {''}
        conflicts = {p: tracked for p, tracked in _dirty_paths(repo_root).items() if p in changed}

        # 충돌 파일의 로컬 내용을 보관하고 HEAD 상태로 되돌려 fast-forward 길을 연다
        saved: dict[str, bytes | None] = {}
        keep: list[str] = []
        for path, tracked in conflicts.items():
            fp = repo_root / path
            local = fp.read_bytes() if fp.exists() else None
            saved[path] = local
            if _is_snapshot(path) and _local_wins(local, _remote_bytes(repo_root, target, path)):
                keep.append(path)
            if tracked:
                _git(repo_root, 'checkout', head, '--', path)
            elif fp.exists():
                fp.unlink()

        try:
            _git(repo_root, 'merge', '--ff-only', '--quiet', target)
        except _GitError:
            for path, local in saved.items():   # 실패 시 작업본을 시도 전 상태로 복구
                fp = repo_root / path
                if local is not None:
                    fp.write_bytes(local)
                elif fp.exists():
                    fp.unlink()
            raise

        for path in keep:
            (repo_root / path).write_bytes(saved[path])
        return SyncResult(at=now, ok=True, updated=True, kept_local=sorted(keep),
                          replaced_local=sorted(set(conflicts) - set(keep)))
    except Exception as e:   # git 부재·타임아웃 포함 — 앱은 계속 떠 있어야 한다
        return SyncResult(at=now, ok=False, error=str(e) or type(e).__name__)
