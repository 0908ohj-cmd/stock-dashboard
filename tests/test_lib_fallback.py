import json
import os
import pathlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB = ROOT / 'scripts' / 'lib_fallback.sh'
GATE = ROOT / 'scripts' / 'check_snapshot_fresh.py'
KST = timezone(timedelta(hours=9))


def _call(snippet, env=None):
    r = subprocess.run(['bash', '-c', f'. "{LIB}" && {snippet}'],
                       capture_output=True, text=True, env={**os.environ, **(env or {})})
    return r.returncode, r.stdout + r.stderr


def _fake_bin(tmp_path, name, body):
    p = tmp_path / name
    p.write_text('#!/bin/bash\n' + body, encoding='utf-8')
    p.chmod(0o755)
    return p


# ── request_fallback ──────────────────────────────────────

def test_request_fallback_dispatches_workflow_for_market(tmp_path):
    calls = tmp_path / 'calls.txt'
    gh = _fake_bin(tmp_path, 'gh', f'echo "$@" >> "{calls}"\n')

    rc, _ = _call('request_fallback kr', env={'GH_BIN': str(gh)})

    assert rc == 0
    assert calls.read_text().split() == [
        'workflow', 'run', 'fetch-data.yml', '--repo', '0908ohj-cmd/stock-dashboard',
        '--ref', 'main', '-f', 'markets=kr']


def test_request_fallback_reports_gh_failure(tmp_path):
    gh = _fake_bin(tmp_path, 'gh', 'echo "HTTP 403: Resource not accessible"; exit 1\n')

    rc, out = _call('request_fallback us', env={'GH_BIN': str(gh)})

    assert rc == 1
    assert 'HTTP 403' in out


def test_request_fallback_rejects_unknown_market_without_calling_gh(tmp_path):
    calls = tmp_path / 'calls.txt'
    gh = _fake_bin(tmp_path, 'gh', f'echo "$@" >> "{calls}"\n')

    rc, out = _call('request_fallback all', env={'GH_BIN': str(gh)})

    assert rc == 1 and '알 수 없는 시장' in out
    assert not calls.exists()


# ── round_is_fresh ────────────────────────────────────────

@pytest.mark.parametrize('gate_output, expected', [
    ('[kr] fresh=true (작업본 기준, 로컬 회차 대비)', 0),
    ('[kr] fresh=false (작업본 기준, 로컬 회차 대비)', 1),
    ('Traceback (most recent call last):\nKeyError', 2),   # 게이트 오류 = 판정 불가
])
def test_round_is_fresh_maps_gate_output(tmp_path, gate_output, expected):
    py = _fake_bin(tmp_path, 'python', f"cat <<'EOF'\n{gate_output}\nEOF\n")

    rc, out = _call(f'round_is_fresh "{py}" kr "{ROOT}"')

    assert rc == expected
    assert gate_output.splitlines()[0] in out


def _gate_root(tmp_path, fetched_at, stock_last, index_last):
    """실제 게이트 스크립트 + 최소 스냅샷으로 된 임시 레포."""
    (tmp_path / 'scripts').mkdir()
    shutil.copy(GATE, tmp_path / 'scripts' / 'check_snapshot_fresh.py')
    ohlcv = tmp_path / 'data' / 'ohlcv'
    ohlcv.mkdir(parents=True)
    for name in ('KR_KOSPI.json', 'KR_KOSDAQ.json'):
        (ohlcv / name).write_text(json.dumps(
            {'fetched_at': fetched_at, 'last_trading_date': stock_last}), encoding='utf-8')
    (ohlcv / 'indices.json').write_text(json.dumps({'data': {
        'KOSPI': {'dates': [index_last]}, 'KOSDAQ': {'dates': [index_last]}}}), encoding='utf-8')
    return tmp_path


def test_round_is_fresh_with_real_gate_fresh(tmp_path):
    now = datetime.now(KST)
    root = _gate_root(tmp_path, now.isoformat(timespec='seconds'), '2026-11-11', '2026-11-11')

    rc, out = _call(f'round_is_fresh python3 kr "{root}"')

    assert rc == 0, out


def test_round_is_fresh_with_real_gate_stock_behind_index(tmp_path):
    """2026-11-10 사례: 배치는 돌았지만 종목이 지수보다 하루 늦음 → 미달(폴백 대상)."""
    now = datetime.now(KST)
    root = _gate_root(tmp_path, now.isoformat(timespec='seconds'), '2026-11-06', '2026-11-09')

    rc, out = _call(f'round_is_fresh python3 kr "{root}"')

    assert rc == 1, out


def test_round_is_fresh_with_real_gate_index_behind_stock(tmp_path):
    """#24 사례: 배치는 돌았지만 지수가 종목보다 하루 늦음 → 미달(폴백 대상)."""
    now = datetime.now(KST)
    root = _gate_root(tmp_path, now.isoformat(timespec='seconds'), '2026-11-11', '2026-11-10')

    rc, out = _call(f'round_is_fresh python3 kr "{root}"')

    assert rc == 1, out
