import pathlib
import subprocess

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'run_batch_local.sh'


def test_script_exists_and_is_executable():
    assert SCRIPT.exists(), f'{SCRIPT} 없음'
    assert SCRIPT.stat().st_mode & 0o111, '실행 권한 없음'


def test_script_syntax_is_valid():
    r = subprocess.run(['bash', '-n', str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_rejects_unknown_market():
    """kr/us 외의 인자는 즉시 거부한다 — 오타로 엉뚱한 수집이 돌면 안 된다."""
    r = subprocess.run(['bash', str(SCRIPT), 'kospi'], capture_output=True, text=True)
    assert r.returncode == 2
    assert '사용법' in (r.stdout + r.stderr)


def test_rejects_missing_market():
    r = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 2
