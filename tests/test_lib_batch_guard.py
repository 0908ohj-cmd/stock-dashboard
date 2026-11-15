"""배치 래퍼 보조 함수(scripts/lib_batch_guard.sh) — #39 M21·L3.

M21: 설치된 launchd plist와 repo plist가 어긋나도 아무도 몰랐다. c0dd7bc(10-19)에서 repo plist를
17:30으로 옮겼지만 KR 배치는 10-20~11-05 내내 16:14에 돌았다 — 래퍼는 reset --hard로 갱신되지만
plist는 재설치해야 반영된다.
"""
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / 'scripts' / 'lib_batch_guard.sh'
RUNNER = ROOT / 'scripts' / 'run_batch_local.sh'
INSTALLER = ROOT / 'scripts' / 'install_batch_launchd.sh'
CI = ROOT / '.github' / 'workflows' / 'tests.yml'


def _call(snippet, env=None):
    r = subprocess.run(['bash', '-c', f'. "{LIB}" && {snippet}'],
                       capture_output=True, text=True, env={**os.environ, **(env or {})})
    return r.returncode, r.stdout + r.stderr


# ── M21 plist 불일치 감지 ─────────────────────────────────────

def _plists(tmp_path, installed: str | None, repo: str | None):
    inst, rep = tmp_path / 'installed.plist', tmp_path / 'repo.plist'
    if installed is not None:
        inst.write_text(installed, encoding='utf-8')
    if repo is not None:
        rep.write_text(repo, encoding='utf-8')
    return inst, rep


def test_plist_같으면_0(tmp_path):
    inst, rep = _plists(tmp_path, '<plist>17:30</plist>', '<plist>17:30</plist>')
    rc, out = _call(f'plist_drift "{inst}" "{rep}"')
    assert rc == 0 and out == ''


def test_plist_다르면_1과_재설치_안내(tmp_path):
    inst, rep = _plists(tmp_path, '<plist>16:10</plist>', '<plist>17:30</plist>')
    rc, out = _call(f'plist_drift "{inst}" "{rep}"')
    assert rc == 1
    assert 'install_batch_launchd.sh' in out


def test_설치된_plist가_없으면_2(tmp_path):
    inst, rep = _plists(tmp_path, None, '<plist/>')
    rc, out = _call(f'plist_drift "{inst}" "{rep}"')
    assert rc == 2 and '설치된 plist 없음' in out


def _lines(path):
    return path.read_text(encoding='utf-8').splitlines()


def test_래퍼는_저장소_동기화_직후_조기_종료_전에_plist를_비교한다():
    """reset --hard로 받은 최신 repo plist와 비교해야 한다 — 그 전이면 옛 plist와 비교한다.
    '이번 회차는 이미 수집됨' 조기 종료보다 앞에 둔다 — 뒤에 두면 그 회차엔 비교를 건너뛴다."""
    lines = _lines(RUNNER)
    reset_at = next(i for i, l in enumerate(lines) if 'git reset --hard origin/main' in l)
    drift_at = next(i for i, l in enumerate(lines) if 'plist_drift' in l)
    guard_at = next(i for i, l in enumerate(lines) if 'round_is_fresh' in l and 'if type' in l)
    assert reset_at < drift_at < guard_at
    block = '\n'.join(lines[drift_at:drift_at + 4])
    assert 'notify' in block


def test_래퍼는_lib_batch_guard를_source한다():
    assert any('lib_batch_guard.sh' in l and l.lstrip().startswith('.') for l in _lines(RUNNER))


def test_설치_스크립트_헤더_시각이_plist와_같다():
    """헤더의 폴백 설명이 KR 16:50으로 낡아 있었다 — 로컬 KR 17:30 / US 08:10, 폴백 KR 18:10 / US 08:50."""
    header = '\n'.join(l for l in _lines(INSTALLER) if l.startswith('#'))
    assert '16:50' not in header
    for t in ('17:30', '08:10', '18:10', '08:50'):
        assert t in header


# ── M21 CI ────────────────────────────────────────────────

def test_CI_워크플로가_PR과_main_push에서_pytest를_돈다():
    text = CI.read_text(encoding='utf-8')
    assert 'pull_request' in text
    assert 'push:' in text and 'main' in text
    assert 'pip install -r requirements.txt pytest' in text
    assert 'python -m pytest tests/' in text


# ── L3 배치 락 경합 ──────────────────────────────────────────
# 예전: ① 건너뛸 때 알림 없이 exit 0 ② mkdir과 pid 기록 사이에 다른 배치가 빈 pid를 '죽은 락'으로
# 보고 회수해 둘이 같이 돌 수 있었고 ③ 늦게 끝난 쪽의 trap이 남의 락을 지웠다 ④ pid가 재사용되면
# 락이 영원히 '살아 있음'이었다.
import time   # noqa: E402

MAX_AGE = 3600


def _acquire(lockdir, env=None):
    rc, out = _call(f'acquire_lock "{lockdir}" {MAX_AGE}; rc=$?; echo "rc=$rc"; '
                    f'echo "msg=$LOCK_MSG"; echo "me=$$"', env)
    fields = dict(l.split('=', 1) for l in out.splitlines() if '=' in l)
    return int(fields['rc']), fields.get('msg', ''), fields.get('me')


def _hold(lockdir, pid, age_sec=0):
    lockdir.mkdir()
    if pid is not None:
        (lockdir / 'pid').write_text(f'{pid}\n')
    if age_sec:
        t = time.time() - age_sec
        os.utime(lockdir, (t, t))


def _dead_pid():
    p = subprocess.Popen(['true'])
    p.wait()
    return p.pid


def test_빈_락은_획득하고_pid를_원자적으로_남긴다(tmp_path):
    lock = tmp_path / 'batch.lock'
    rc, msg, me = _acquire(lock)
    assert rc == 0 and msg == ''
    assert (lock / 'pid').read_text().strip() == me
    assert [p.name for p in lock.iterdir()] == ['pid']          # 임시 파일이 남지 않는다


def test_살아_있는_배치의_락은_건너뛴다(tmp_path):
    lock = tmp_path / 'batch.lock'
    _hold(lock, os.getpid())
    rc, msg, _ = _acquire(lock)
    assert rc == 1 and '실행 중' in msg and str(os.getpid()) in msg
    assert (lock / 'pid').read_text().strip() == str(os.getpid())   # 남의 락은 그대로


def test_pid_기록_전의_젊은_락은_죽은_락으로_보지_않는다(tmp_path):
    """mkdir 직후 pid를 쓰기 전 — 예전엔 빈 pid를 죽은 락으로 보고 회수해 둘이 같이 돌았다."""
    lock = tmp_path / 'batch.lock'
    _hold(lock, None)
    rc, msg, _ = _acquire(lock)
    assert rc == 1 and '건너뜀' in msg
    assert lock.exists() and not (lock / 'pid').exists()


def test_pid_없이_오래된_락은_회수한다(tmp_path):
    lock = tmp_path / 'batch.lock'
    _hold(lock, None, age_sec=600)
    rc, msg, me = _acquire(lock)
    assert rc == 0 and '회수' in msg
    assert (lock / 'pid').read_text().strip() == me


def test_죽은_pid의_락은_회수한다(tmp_path):
    lock = tmp_path / 'batch.lock'
    dead = _dead_pid()
    _hold(lock, dead)
    rc, msg, me = _acquire(lock)
    assert rc == 0 and '회수' in msg and str(dead) in msg
    assert (lock / 'pid').read_text().strip() == me


def test_상한을_넘긴_락은_pid가_살아_있어도_회수한다(tmp_path):
    """pid 재사용·멈춘 배치가 이후 회차를 영구히 막지 않게."""
    lock = tmp_path / 'batch.lock'
    _hold(lock, os.getpid(), age_sec=MAX_AGE + 60)
    rc, msg, me = _acquire(lock)
    assert rc == 0 and '상한' in msg
    assert (lock / 'pid').read_text().strip() == me


def test_release_lock은_내_락만_지운다(tmp_path):
    lock = tmp_path / 'batch.lock'
    _hold(lock, os.getpid())                                      # 남의 락 (회수 뒤 새 배치의 락)
    rc, _ = _call(f'release_lock "{lock}"')
    assert rc == 0 and lock.exists()

    rc, out = _call(f'acquire_lock "{tmp_path / "mine"}" {MAX_AGE} && release_lock "{tmp_path / "mine"}" '
                    f'&& [ ! -e "{tmp_path / "mine"}" ] && echo released')
    assert 'released' in out


def test_래퍼는_건너뛸_때도_알리고_trap은_release_lock을_쓴다():
    text = RUNNER.read_text(encoding='utf-8')
    block = text[text.index('acquire_lock "$LOCKDIR"'):]
    block = block[:block.index('esac')]
    skip = block[block.index('1)'):]
    assert 'notify' in skip.split(';;')[0]
    assert "trap 'release_lock \"$LOCKDIR\"' EXIT" in text
    assert 'rm -rf "$LOCKDIR"' not in text                        # 무조건 지우는 옛 trap·회수 제거


def test_동시에_기동해도_하나만_락을_잡는다(tmp_path):
    """예전엔 mkdir 직후 pid 기록 전의 빈 pid를 다른 배치가 '죽은 락'으로 회수해 둘이 같이 돌 수 있었다."""
    lock = tmp_path / 'batch.lock'
    script = (f'. "{LIB}"; acquire_lock "{lock}" {MAX_AGE}; rc=$?; echo "rc=$rc"; '
              f'[ $rc -eq 0 ] && sleep 1; exit 0')
    procs = [subprocess.Popen(['bash', '-c', script], stdout=subprocess.PIPE, text=True) for _ in range(8)]
    rcs = [p.communicate()[0].strip() for p in procs]
    assert rcs.count('rc=0') == 1 and rcs.count('rc=1') == 7
