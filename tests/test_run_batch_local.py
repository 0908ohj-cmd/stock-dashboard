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


def test_raises_open_file_limit_before_builders():
    """유니버스 빌드 전에 파일 열기 한도를 올린다.

    launchd의 기본 한도는 256이다(`launchctl limit maxfiles`). 유니버스 빌더는
    yfinance로 수백 종목을 스레드로 한꺼번에 받으며 소켓·캐시 DB를 동시에 열어
    이 한도를 넘기고, `unable to open database file`로 무너진다. 대화형 셸은
    한도가 커서 dry-run에서는 드러나지 않는다(2026-09-28 첫 launchd 실행에서 발견,
    ulimit 256 → 실패 / 4096 → 성공으로 재현 확인).
    """
    lines = SCRIPT.read_text(encoding='utf-8').splitlines()
    ulimit_at = next((i for i, l in enumerate(lines) if l.strip().startswith('ulimit -n')), None)
    builder_at = next(i for i, l in enumerate(lines) if '"$BUILDER"' in l)

    assert ulimit_at is not None, 'ulimit -n 설정이 없다'
    assert ulimit_at < builder_at, 'ulimit -n이 빌더 실행보다 뒤에 있다'


def test_rejects_missing_market():
    r = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 2


# ── 폴백 즉시 실행 연결 (lib_fallback.sh) ─────────────────────

def _lines():
    return SCRIPT.read_text(encoding='utf-8').splitlines()


def test_fallback_lib_is_loaded_before_die_is_defined():
    """die는 env 로드 전에도 불린다 — 라이브러리가 그보다 먼저 로드돼야 폴백을 띄울 수 있다."""
    lines = _lines()
    source_at = next(i for i, l in enumerate(lines) if 'lib_fallback.sh' in l and l.lstrip().startswith('.'))
    die_at = next(i for i, l in enumerate(lines) if l.startswith('die()'))
    assert source_at < die_at


def test_die_requests_fallback():
    die = next(l for l in _lines() if l.startswith('die()'))
    assert 'fallback_now' in die
    assert die.index('fallback_now') < die.index('exit 1')


def test_every_success_exit_verifies_round():
    """성공 종료 두 곳(변경 없음·완료) 모두 회차 판정을 거친다 — 늦은 데이터를 올리고 끝나면 안 된다."""
    lines = _lines()
    no_change = next(i for i, l in enumerate(lines) if '변경 없음 — 종료' in l)
    done = next(i for i, l in enumerate(lines) if l.strip() == 'log "완료"')
    assert lines[no_change + 1].strip() == 'verify_round'
    assert lines[no_change + 2].strip() == 'exit 0'
    assert lines[done + 1].strip() == 'verify_round'


def test_dry_run_never_requests_fallback():
    text = SCRIPT.read_text(encoding='utf-8')
    body = text[text.index('fallback_now() {'):]
    body = body[:body.index('\n}\n')]
    assert '[ "$DRY_RUN" = "--dry-run" ] && return 0' in body
