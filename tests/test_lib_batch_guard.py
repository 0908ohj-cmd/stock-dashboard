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


def _lock_skip_branch():
    text = RUNNER.read_text(encoding='utf-8')
    block = text[text.index('acquire_lock "$LOCKDIR"'):]
    block = block[:block.index('esac')]
    return block[block.index('1)'):].split(';;')[0]


def test_래퍼는_락_때문에_건너뛰면_0이_아닌_전용_코드로_끝낸다():
    """exit 0이면 stockEdge job_wrapper가 성공으로 기록해 catchup이 놓친 회차를 다시 돌리지 않는다
    (catchup.classify: 회차 이후 성공 기록이 있으면 skip-on-time)."""
    skip = _lock_skip_branch()
    assert 'exit "$LOCK_BUSY_EXIT"' in skip and 'exit 0' not in skip
    line = next(l for l in _lines(RUNNER) if l.startswith('LOCK_BUSY_EXIT='))
    code = int(line.split('=', 1)[1].split()[0])
    # 래퍼 die·finish 1, 사용법 2, fetch_snapshot 지수 실패 3, job_wrapper 사용법 2·신호 130/143,
    # 셸 예약 126/127/128+n과 겹치지 않는다
    assert code not in (0, 1, 2, 3) and 3 < code < 126


def test_래퍼는_건너뛸_때_알림을_job_wrapper에_맡기고_trap은_release_lock을_쓴다():
    """실패 종료가 되므로 job_wrapper가 알린다 — 직접 실행할 때만 스스로 알린다(notify_failure)."""
    assert 'notify_failure' in _lock_skip_branch()
    text = RUNNER.read_text(encoding='utf-8')
    assert "trap 'release_lock \"$LOCKDIR\"' EXIT" in text
    assert 'rm -rf "$LOCKDIR"' not in text                        # 무조건 지우는 옛 trap·회수 제거


def _race_lock(tmp_path, n=8, pre=''):
    """n개를 출발 신호에 맞춰 동시에 acquire_lock 시키고 각자의 rc 목록을 돌려준다. 락을 잡은 쪽은 n개가
    모두 결과를 남길 때까지 살아 있는다(주인이 죽어 정상 회수되는 경우를 배제). pre는 lib을 읽은 뒤 실행할 셸."""
    lock, go, out = tmp_path / 'batch.lock', tmp_path / 'go', tmp_path / 'rc'
    out.mkdir()
    script = (f'. "{LIB}"; {pre} '
              f'while [ ! -e "{go}" ]; do sleep 0.01; done; '
              f'acquire_lock "{lock}" {MAX_AGE}; rc=$?; echo "$rc" > "{out}/rc.$$"; '
              f'if [ $rc -eq 0 ]; then '
              f'  for _ in $(seq 1 1500); do '
              f'    [ "$(ls "{out}" | wc -l)" -ge {n} ] && break; sleep 0.01; '
              f'  done; '
              f'fi; '
              f'echo "rc=$rc"')
    procs = [subprocess.Popen(['bash', '-c', script], stdout=subprocess.PIPE, text=True) for _ in range(n)]
    go.touch()
    return [p.communicate(timeout=30)[0].strip() for p in procs]


def test_pid_기록이_늦어도_하나만_락을_잡는다(tmp_path):
    """mkdir과 pid 기록 사이 틈을 0.3초로 벌려 그 틈에 들어온 배치를 확실히 만든다 — 빈 pid 락을
    '잡는 중'(LOCK_PID_GRACE_SEC)으로 보지 않고 회수하면 여기서 rc=0이 둘 이상 나온다."""
    slow = ('eval "_orig_$(declare -f _write_lock_pid)"; '      # 실제 pid 기록 함수 앞에 지연만 끼운다
            '_write_lock_pid() { sleep 0.3; _orig__write_lock_pid "$@"; };')
    rcs = _race_lock(tmp_path, pre=slow)
    assert rcs.count('rc=0') == 1 and rcs.count('rc=1') == 7, rcs


def test_동시에_기동해도_하나만_락을_잡는다(tmp_path):
    """예전엔 mkdir 직후 pid 기록 전의 빈 pid를 다른 배치가 '죽은 락'으로 회수해 둘이 같이 돌 수 있었다.

    8개가 출발 신호(go)를 기다렸다 한꺼번에 락을 시도하고, 락을 잡은 쪽은 8개가 모두 결과를 남길 때까지
    살아 있는다. CI(Ubuntu 26.04)의 uutils mkdir 0.10은 동시에 실행하면 둘 다 0을 돌려줘 주인이 둘
    나왔다(2026-11-23 측정 600회 중 122회) — pid 파일의 O_EXCL 생성이 주인을 하나로 가른다."""
    rcs = _race_lock(tmp_path)
    assert rcs.count('rc=0') == 1 and rcs.count('rc=1') == 7, rcs


def _non_exclusive_mkdir(tmp_path):
    """이미 있는 디렉터리에도 0을 돌려주는 mkdir — uutils mkdir 0.10이 동시 실행 때 보이는 동작을 결정적으로
    재현한다. 새로 만들 때는 경쟁 배치(이 테스트 프로세스)가 그 사이 pid까지 먼저 쓴 상황으로 만든다."""
    bin_dir = tmp_path / 'fake-bin'
    bin_dir.mkdir()
    fake = bin_dir / 'mkdir'
    fake.write_text('#!/bin/bash\n'
                    '[ -e "$1" ] && exit 0\n'
                    f'/bin/mkdir "$1" && echo {os.getpid()} > "$1/pid"\n')
    fake.chmod(0o755)
    return {'PATH': f'{bin_dir}:{os.environ["PATH"]}'}


def test_mkdir가_이미_있는_락에_성공을_돌려줘도_주인을_빼앗지_않는다(tmp_path):
    lock = tmp_path / 'batch.lock'
    _hold(lock, os.getpid())
    rc, msg, _ = _acquire(lock, _non_exclusive_mkdir(tmp_path))
    assert rc == 1 and '실행 중' in msg
    assert (lock / 'pid').read_text().strip() == str(os.getpid())


def test_죽은_락_회수_직후_다른_배치가_pid를_먼저_쓰면_건너뛴다(tmp_path):
    lock = tmp_path / 'batch.lock'
    _hold(lock, _dead_pid())
    rc, msg, _ = _acquire(lock, _non_exclusive_mkdir(tmp_path))
    assert rc == 1 and '먼저 잡음' in msg
    assert (lock / 'pid').read_text().strip() == str(os.getpid())


def test_래퍼의_락_분기를_실행하면_건너뛸_때_LOCK_BUSY_EXIT로_끝난다(tmp_path):
    """run_batch_local.sh의 락 case 블록을 그대로 떼어 실행한다(env·수집 없이, log·알림은 대역)."""
    text = RUNNER.read_text(encoding='utf-8')
    const = next(l for l in text.splitlines() if l.startswith('LOCK_BUSY_EXIT='))
    block = text[text.index('acquire_lock "$LOCKDIR"'):]
    block = block[:block.index('esac') + len('esac')]
    lock = tmp_path / 'batch.lock'
    _hold(lock, os.getpid())                                      # 다른 배치가 실행 중
    harness = (f'. "{LIB}"; {const}; LOCKDIR="{lock}"; LOCK_MAX_AGE_SEC=3600; '
               'log() { echo "LOG $*"; }; notify_failure() { echo "ALERT $*"; }; '
               'die() { echo "DIE $*"; exit 1; }\n' + block + '\necho reached')
    r = subprocess.run(['bash', '-c', harness], capture_output=True, text=True)
    assert r.returncode == 75
    assert 'ALERT 이번 회차 건너뜀' in r.stdout and 'reached' not in r.stdout

    lock2 = tmp_path / 'free.lock'
    r = subprocess.run(['bash', '-c', harness.replace(str(lock), str(lock2))], capture_output=True, text=True)
    assert r.returncode == 0 and 'reached' in r.stdout
