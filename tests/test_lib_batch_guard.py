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


def test_래퍼는_저장소_동기화_뒤_수집_전에_plist를_비교한다():
    """reset --hard로 받은 최신 repo plist와 비교해야 한다 — 그 전이면 옛 plist와 비교한다."""
    lines = _lines(RUNNER)
    reset_at = next(i for i, l in enumerate(lines) if 'git reset --hard origin/main' in l)
    drift_at = next(i for i, l in enumerate(lines) if 'plist_drift' in l)
    fetch_at = next(i for i, l in enumerate(lines) if 'scripts/fetch_snapshot.py' in l)
    assert reset_at < drift_at < fetch_at
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
