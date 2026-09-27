import os
import pathlib
import subprocess

import pytest

LIB = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'lib_git_push.sh'

ENV = {
    **os.environ,
    'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
    'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t',
    'PUSH_RETRY_SLEEP': '0',
}


def _git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, env=ENV,
                          check=True, capture_output=True, text=True)


def _commit(cwd, name, content):
    (cwd / name).write_text(content, encoding='utf-8')
    _git(cwd, 'add', name)
    _git(cwd, 'commit', '-q', '-m', f'add {name}')


def _push_with_rebase(cwd):
    r = subprocess.run(['bash', '-c', f'. "{LIB}" && push_with_rebase'],
                       cwd=cwd, env=ENV, capture_output=True, text=True)
    return r.returncode


@pytest.fixture
def two_clones(tmp_path):
    """원격(bare) 하나와 그것을 각자 클론한 배치·타 작업자 두 체크아웃."""
    remote = tmp_path / 'remote.git'
    _git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(remote))

    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(seed, 'init', '-q', '-b', 'main')
    _commit(seed, 'README', 'seed')
    _git(seed, 'remote', 'add', 'origin', str(remote))
    _git(seed, 'push', '-q', 'origin', 'main')

    batch, other = tmp_path / 'batch', tmp_path / 'other'
    _git(tmp_path, 'clone', '-q', str(remote), str(batch))
    _git(tmp_path, 'clone', '-q', str(remote), str(other))
    return remote, batch, other


def test_plain_push_is_rejected_when_remote_moved(two_clones):
    """전제 검증 — rebase 없이 push하면 실제로 거절된다.

    이 테스트가 통과해야 아래 테스트가 '거절될 상황을 살려냈다'는 의미를 갖는다.
    """
    _, batch, other = two_clones
    _commit(other, 'leaderboard.json', 'lb')
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(batch, 'snapshot.json', 'snap')

    r = subprocess.run(['git', 'push', '-q', 'origin', 'main'],
                       cwd=batch, env=ENV, capture_output=True, text=True)
    assert r.returncode != 0


def test_rebases_when_remote_moved_ahead(two_clones):
    """수집 도중 다른 커밋이 원격에 올라와도 rebase로 얹어 push한다.

    stockEdge의 리더보드 US 커밋이 매일 07:01~07:05에 들어와(2026-09 실측)
    07:00 US 배치의 push와 정면으로 겹친다. rebase 없이는 매일 거절된다.
    """
    remote, batch, other = two_clones
    _commit(other, 'leaderboard.json', 'lb')    # 리더보드가 먼저 push
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(batch, 'snapshot.json', 'snap')     # 배치는 옛 base 위에 커밋

    assert _push_with_rebase(batch) == 0

    subjects = _git(remote, 'log', '--format=%s', 'main').stdout.splitlines()
    assert 'add snapshot.json' in subjects      # 배치 커밋이 올라갔고
    assert 'add leaderboard.json' in subjects   # 남의 커밋도 보존됐다


def test_conflict_returns_1_and_leaves_no_rebase_in_progress(two_clones):
    """같은 파일을 서로 다르게 고쳐 충돌하면 1을 반환하고 rebase를 중단해 둔다.

    rebase 진행 상태가 남으면 다음 날 배치의 pull --rebase가 전부 막힌다.
    """
    _, batch, other = two_clones
    _commit(other, 'same.json', 'theirs')
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(batch, 'same.json', 'ours')

    assert _push_with_rebase(batch) == 1
    assert not (batch / '.git' / 'rebase-merge').exists()
    assert not (batch / '.git' / 'rebase-apply').exists()


def test_token_stays_literal_in_git_argv(tmp_path):
    """credential.helper로 넘길 때 토큰이 argv에서 펼쳐지지 않는다.

    push_with_rebase는 헬퍼 값을 `git -c "credential.helper=$cred"` 배열로 넘긴다.
    $cred 안의 `$DASHBOARD_GITHUB_TOKEN`이 bash 단계에서 펼쳐지면 토큰이
    ps 출력에 그대로 드러난다. git이 헬퍼를 실행할 때에만 펼쳐져야 한다.
    """
    env = {**ENV, 'DASHBOARD_GITHUB_TOKEN': 'SECRET_MUST_NOT_APPEAR'}
    script = (
        '. "%s"; '
        'cred=\'!f() { echo "password=$DASHBOARD_GITHUB_TOKEN"; }; f\'; '
        'gitc=(git -c "credential.helper=$cred"); '
        '"${gitc[@]}" config --get credential.helper'
    ) % LIB
    r = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env,
                       capture_output=True, text=True)

    assert r.returncode == 0, r.stderr
    assert '$DASHBOARD_GITHUB_TOKEN' in r.stdout       # 리터럴로 전달됐고
    assert 'SECRET_MUST_NOT_APPEAR' not in r.stdout    # 값은 argv에 없다


def test_noop_when_already_up_to_date(two_clones):
    """원격이 안 움직였으면 그냥 push된다 — 평소 경로."""
    remote, batch, _ = two_clones
    _commit(batch, 'snapshot.json', 'snap')

    assert _push_with_rebase(batch) == 0
    subjects = _git(remote, 'log', '--format=%s', 'main').stdout.splitlines()
    assert subjects[0] == 'add snapshot.json'
