import json
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

SNAP = 'data/ohlcv/KR_KOSPI.json'
IDX = 'data/ohlcv/indices.json'


def _git(cwd, *args, check=True):
    return subprocess.run(['git', *args], cwd=cwd, env=ENV,
                          check=check, capture_output=True, text=True)


def _write(cwd, rel, content):
    p = cwd / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')


def _commit(cwd, rel, content, msg=None):
    _write(cwd, rel, content)
    _git(cwd, 'add', rel)
    _git(cwd, 'commit', '-q', '-m', msg or f'add {rel}')


def _rec(dates, close):
    n = len(dates)
    return {'dates': dates, 'open': [close] * n, 'high': [close + 1] * n,
            'low': [close - 1] * n, 'close': [close] * n, 'volume': [1000.0] * n}


def _snap(market, fetched_at, ltd, data):
    """한 줄짜리 JSON — store.save_snapshot과 같은 모양(줄바꿈 없음)."""
    return json.dumps({'market': market, 'fetched_at': fetched_at,
                       'last_trading_date': ltd, 'ticker_count': len(data),
                       'failed': [], 'data': data}, ensure_ascii=False)


def _sync_and_push(cwd):
    r = subprocess.run(['bash', '-c', f'. "{LIB}" && sync_and_push'],
                       cwd=cwd, env=ENV, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _remote_file(remote, rel):
    return _git(remote, 'show', f'main:{rel}').stdout


@pytest.fixture
def two_clones(tmp_path):
    """원격(bare) 하나, 그것을 각자 클론한 로컬 배치·다른 작업자(Action 등)."""
    remote = tmp_path / 'remote.git'
    _git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(remote))

    seed = tmp_path / 'seed'
    seed.mkdir()
    _git(seed, 'init', '-q', '-b', 'main')
    _commit(seed, SNAP, _snap('KR_KOSPI', '2026-09-15T16:44:00+09:00', '2026-09-15',
                              {'005930': _rec(['2026-09-15'], 100.0)}))
    _commit(seed, IDX, _snap('indices', '2026-09-15T16:44:00+09:00', '2026-09-15', {
        'KOSPI': _rec(['2026-09-15'], 7000.0),
        'NASDAQ': _rec(['2026-09-14'], 26000.0),
    }))
    _git(seed, 'remote', 'add', 'origin', str(remote))
    _git(seed, 'push', '-q', 'origin', 'main')

    local, other = tmp_path / 'local', tmp_path / 'other'
    _git(tmp_path, 'clone', '-q', str(remote), str(local))
    _git(tmp_path, 'clone', '-q', str(remote), str(other))
    return remote, local, other


# ── 전제 검증 ───────────────────────────────────────────────

def test_premise_plain_push_is_rejected_when_remote_moved(two_clones):
    """원격이 앞서 나가면 일반 push는 거절된다."""
    _, local, other = two_clones
    _commit(other, 'data/leaderboard/us.json', 'lb')
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(local, 'data/saved/kospi_10ema.tickers', '005930')

    assert _git(local, 'push', '-q', 'origin', 'main', check=False).returncode != 0


def test_premise_rebase_conflicts_on_single_line_snapshot(two_clones):
    """한 줄짜리 스냅샷 JSON은 양쪽이 다르게 쓰면 git rebase가 충돌한다.

    이전 구현(pull --rebase)이 요구사항 '로컬이 늦어도 push에 문제없어야 함'을
    어기던 이유다.
    """
    _, local, other = two_clones
    _commit(other, SNAP, _snap('KR_KOSPI', '2026-09-16T20:05:00+09:00', '2026-09-16',
                               {'000660': _rec(['2026-09-16'], 200.0)}))
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(local, SNAP, _snap('KR_KOSPI', '2026-09-16T20:08:00+09:00', '2026-09-16',
                               {'005930': _rec(['2026-09-16'], 100.0)}))

    r = _git(local, 'pull', '--rebase', '-q', 'origin', 'main', check=False)
    assert r.returncode != 0
    _git(local, 'rebase', '--abort', check=False)


# ── 요구사항 3: 멱등성 ──────────────────────────────────────

def test_local_push_succeeds_after_action_pushed_same_snapshot(two_clones):
    """Action 폴백이 먼저 같은 날 스냅샷을 push했어도 로컬 push가 성공한다.

    로컬이 늦게 돌아 Action이 먼저 수집·push한 뒤, 로컬이 자기 수집분을 push하는
    상황. 두 스냅샷은 종목 단위로 병합돼야 한다.
    """
    remote, local, other = two_clones
    _commit(other, SNAP, _snap('KR_KOSPI', '2026-09-16T20:05:00+09:00', '2026-09-16', {
        '005930': _rec(['2026-09-15', '2026-09-16'], 100.0),
        '000660': _rec(['2026-09-15', '2026-09-16'], 200.0),
    }), msg='data: kr 스냅샷 2026-09-16 (Action 폴백)')
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(local, SNAP, _snap('KR_KOSPI', '2026-09-16T20:08:00+09:00', '2026-09-16', {
        '005930': _rec(['2026-09-15', '2026-09-16'], 100.0),
        '035720': _rec(['2026-09-15', '2026-09-16'], 300.0),   # 새 유니버스 종목
    }), msg='data: kr 스냅샷 2026-09-16')

    code, out = _sync_and_push(local)

    assert code == 0, out
    merged = json.loads(_remote_file(remote, SNAP))
    assert set(merged['data']) == {'005930', '000660', '035720'}
    assert merged['fetched_at'] == '2026-09-16T20:08:00+09:00'
    subjects = _git(remote, 'log', '--format=%s', 'main').stdout.splitlines()
    assert subjects[0] == 'data: kr 스냅샷 2026-09-16'            # 로컬 커밋 메시지 보존
    assert 'data: kr 스냅샷 2026-09-16 (Action 폴백)' in subjects  # 폴백 커밋도 보존


def test_cross_market_indices_race_keeps_both(two_clones):
    """KR 배치와 US 배치가 indices.json을 동시에 고쳐도 양쪽 지수 갱신이 모두 남는다."""
    remote, local, other = two_clones
    _commit(other, IDX, _snap('indices', '2026-09-16T13:04:00+09:00', '2026-09-15', {
        'KOSPI': _rec(['2026-09-15'], 7000.0),
        'NASDAQ': _rec(['2026-09-14', '2026-09-15'], 26000.0),     # US 배치가 NASDAQ 갱신
    }))
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(local, IDX, _snap('indices', '2026-09-16T16:44:00+09:00', '2026-09-16', {
        'KOSPI': _rec(['2026-09-15', '2026-09-16'], 7000.0),       # KR 배치가 KOSPI 갱신
        'NASDAQ': _rec(['2026-09-14'], 26000.0),
    }))

    code, out = _sync_and_push(local)

    assert code == 0, out
    merged = json.loads(_remote_file(remote, IDX))
    assert merged['data']['KOSPI']['dates'][-1] == '2026-09-16'
    assert merged['data']['NASDAQ']['dates'][-1] == '2026-09-15'


def test_repeated_run_is_harmless(two_clones):
    """이미 반영된 상태에서 다시 돌려도 성공하고 새 커밋을 만들지 않는다."""
    remote, local, _ = two_clones
    _commit(local, 'data/saved/kospi_10ema.tickers', '005930')
    assert _sync_and_push(local)[0] == 0
    head = _git(remote, 'rev-parse', 'main').stdout

    assert _sync_and_push(local)[0] == 0
    assert _git(remote, 'rev-parse', 'main').stdout == head


# ── 그 밖의 경로 ───────────────────────────────────────────

def test_unrelated_remote_commit_is_kept(two_clones):
    """리더보드처럼 다른 파일을 건드린 원격 커밋은 그대로 두고 위에 얹는다."""
    remote, local, other = two_clones
    _commit(other, 'data/leaderboard/us.json', 'lb')
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(local, 'data/saved/kospi_10ema.tickers', '005930')

    assert _sync_and_push(local)[0] == 0
    assert _remote_file(remote, 'data/leaderboard/us.json') == 'lb'
    assert _remote_file(remote, 'data/saved/kospi_10ema.tickers') == '005930'


def test_non_snapshot_conflict_keeps_ours(two_clones):
    """스냅샷이 아닌 파일(티커 목록)이 양쪽에서 달라지면 push하는 쪽 것을 쓴다.

    같은 날 같은 알고리즘으로 만든 유니버스라 사실상 같다. 병합할 수 없는
    형식이므로 막히지 않고 끝나는 쪽을 택한다.
    """
    remote, local, other = two_clones
    _commit(other, 'data/saved/kospi_10ema.tickers', '000660')
    _git(other, 'push', '-q', 'origin', 'main')
    _commit(local, 'data/saved/kospi_10ema.tickers', '005930')

    assert _sync_and_push(local)[0] == 0
    assert _remote_file(remote, 'data/saved/kospi_10ema.tickers') == '005930'


def test_noop_when_already_up_to_date(two_clones):
    """원격이 안 움직였으면 그대로 push된다 — 평소 경로."""
    remote, local, _ = two_clones
    _commit(local, 'data/saved/kospi_10ema.tickers', '005930', msg='data: kr 스냅샷')

    assert _sync_and_push(local)[0] == 0
    assert _git(remote, 'log', '-1', '--format=%s', 'main').stdout.strip() == 'data: kr 스냅샷'


def test_token_stays_literal_in_git_argv(tmp_path):
    """credential.helper로 넘길 때 토큰이 argv에서 펼쳐지지 않는다.

    sync_and_push는 헬퍼 값을 `git -c "credential.helper=$cred"` 배열로 넘긴다.
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
    assert '$DASHBOARD_GITHUB_TOKEN' in r.stdout
    assert 'SECRET_MUST_NOT_APPEAR' not in r.stdout
