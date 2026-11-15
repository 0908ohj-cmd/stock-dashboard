import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).parent.parent / 'scripts'


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


refresh_sectors = _load_script('refresh_sectors')
refresh_yf_sectors = _load_script('refresh_yf_sectors')

import data
from data import leaderboard_store, store, theme_classifier


def test_select_targets_default_uncached_only():
    """기본 모드: 캐시에 없는(또는 theme/detail 둘 다 빈) 종목만 대상."""
    cache = {
        'AAA': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2020-01-01T00:00:00'},
        'BBB': {'theme': '', 'detail': '', 'updated': '2026-01-01T00:00:00'},
    }
    out = refresh_sectors._select_targets(['AAA', 'BBB', 'CCC'], cache,
                                          all_mode=False, stale_days=None)
    assert out == ['BBB', 'CCC']


def test_select_targets_stale_days():
    """--stale-days N: 미캐시 + updated가 N일 이전인 종목."""
    old = (datetime.now() - timedelta(days=10)).isoformat()
    new = datetime.now().isoformat()
    cache = {
        'OLD': {'theme': '기타', 'detail': 'x', 'updated': old},
        'NEW': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': new},
    }
    out = refresh_sectors._select_targets(['OLD', 'NEW', 'MISS'], cache,
                                          all_mode=False, stale_days=7)
    assert out == ['OLD', 'MISS']


def test_select_targets_all_mode():
    cache = {'AAA': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2026-01-01T00:00:00'}}
    out = refresh_sectors._select_targets(['AAA', 'BBB'], cache, all_mode=True, stale_days=None)
    assert out == ['AAA', 'BBB']


def test_run_saves_cache_once_and_resolves_misc(tmp_path, monkeypatch):
    """병렬 분류 → 성공분 캐시 반영, '기타'+detail은 resolve 배치, 저장은 마지막 1회."""
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    saves = []
    real_save = theme_classifier._save_cache
    monkeypatch.setattr(theme_classifier, '_save_cache',
                        lambda c: (saves.append(1), real_save(c)))

    seen_markets = {}

    def fake_classify_one(ticker, name_hint, themes, market):
        seen_markets[ticker] = market
        return {'AAA': {'theme': '메모리 반도체', 'detail': 'HBM'},
                'MSC': {'theme': '기타', 'detail': '신규 테마 후보'},
                'BAD': None}[ticker]

    monkeypatch.setattr(theme_classifier, '_classify_one', fake_classify_one)
    monkeypatch.setattr(theme_classifier, '_resolve_misc',
                        lambda tickers, cur, themes: {'MSC': {'theme': '신테마', 'detail': '신규 테마 후보'}})

    themes = [{'name': '메모리 반도체', 'description': '...'}]
    markets = {'AAA': 'US', 'MSC': 'KR_KOSDAQ', 'BAD': 'KR_KOSPI'}
    failed = refresh_sectors._classify_targets(['AAA', 'MSC', 'BAD'], themes, {}, workers=2, markets=markets)

    saved = json.loads(cache_file.read_text(encoding='utf-8'))
    assert saved['AAA']['detail'] == 'HBM'
    assert saved['MSC']['theme'] == '신테마'
    assert 'BAD' not in saved          # 완전 실패는 미저장 (자가치유)
    assert failed == ['BAD']
    assert len(saves) == 1             # 저장은 마지막 1회
    assert seen_markets == markets     # 프롬프트가 시장을 알도록 종목별 시장을 넘긴다 (#45 L6)


# ── 구 갱신 스크립트 안전화 (#45 M19) ─────────────────────────────

@pytest.fixture
def saved_dir(tmp_path, monkeypatch):
    """data/saved 대역 — 현행 티커 파일(store.SNAPSHOT_TICKER_FILES)과 옛 파일(10ema_kr.tickers)."""
    d = tmp_path / 'saved'
    d.mkdir()
    for name, lines in {'kospi.tickers': ['KOSPI', '005930'],
                        'kosdaq.tickers': ['KOSDAQ', '247540', '0015S0'],
                        'kosdaq_10ema.tickers': ['247540'],
                        'us.tickers': ['NVDA'],
                        'us_sector.tickers': ['NVDA', 'QQQ'],
                        '10ema_kr.tickers': ['111111']}.items():   # SNAPSHOT_TICKER_FILES에 없는 옛 파일
        (d / name).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    monkeypatch.setattr(store, 'SAVED_DIR', d)
    return d


@pytest.fixture
def no_universe(monkeypatch):
    """KRX 유니버스(pykrx)·S&P500·리더보드 조회를 기록하는 가짜 — 호출되면 calls에 남는다."""
    calls = []

    def rec(name, value):
        def f(*a, **k):
            calls.append(name)
            return value
        return f

    fake_pykrx = types.ModuleType('pykrx')
    fake_pykrx.__getattr__ = lambda name: calls.append(f'pykrx.{name}') or rec(f'pykrx.{name}', None)
    monkeypatch.setitem(sys.modules, 'pykrx', fake_pykrx)
    fake_universe = types.ModuleType('data.universe')
    fake_universe.get_kr_universe = rec('get_kr_universe', ['999999'])
    fake_universe.get_us_universe = rec('get_us_universe', ['SPY'])
    monkeypatch.setitem(sys.modules, 'data.universe', fake_universe)
    monkeypatch.setattr(data, 'universe', fake_universe, raising=False)
    monkeypatch.setattr(leaderboard_store, 'get_tickers', rec('leaderboard', ['LBRD']))
    return calls


_EXPECTED_PAIRS = [('0015S0', 'KR_KOSDAQ'), ('005930', 'KR_KOSPI'), ('247540', 'KR_KOSDAQ'),
                   ('KOSDAQ', 'KR_KOSDAQ'), ('KOSPI', 'KR_KOSPI'), ('NVDA', 'US'), ('QQQ', 'US')]


def test_테마_갱신_대상은_saved_현행_티커파일_합집합이고_KRX를_조회하지_않는다(saved_dir, no_universe):
    """예전엔 KRX 시총 상위 200×2(get_kr_universe.__wrapped__ → pykrx 직접)·S&P500·리더보드까지 모아
    1417종목이 대상이었다. 지금 화면에 쓰이는 종목은 data/saved의 현행 티커 파일뿐이다."""
    assert refresh_sectors._collect_targets() == _EXPECTED_PAIRS
    assert no_universe == []


def test_yf_갱신_대상은_saved_현행_티커파일_합집합이고_KRX를_조회하지_않는다(saved_dir, no_universe):
    """yf 스크립트는 라운드(최대 30회)마다 KRX 유니버스를 다시 조회했다. 시장은 파일이 정한다 —
    영숫자 KR 코드(0015S0)도 KR_KOSDAQ (#45 L6)."""
    assert refresh_yf_sectors._collect_targets() == _EXPECTED_PAIRS
    assert no_universe == []


# 셸 래퍼는 tmp의 가짜 저장소에 복사해 돌린다 — git·claude·python을 모두 대역으로 바꿔
# 실제 분류·조회·git 명령은 하나도 실행되지 않는다. 대역 python은 받은 인자와 그 순간의 락 형태를 기록한다.
_FAKE_PY = """#!/usr/bin/env bash
d="$(cd "$(dirname "$0")/../.." && pwd)"
kind=none
for l in "$d"/.refresh_*.lock; do [ -d "$l" ] && kind=dir; [ -f "$l" ] && kind=file; done
if [ "${1:-}" = "-c" ]; then   # yfinance 리밋 프로브 — 코드 본문은 기록하지 않는다
    echo "lock=$kind args=-c" >> "$d/py_calls.log"
    exit 0
fi
echo "lock=$kind args=$*" >> "$d/py_calls.log"
echo "수집할 종목 없음"
"""


def _fake_repo(tmp_path, script):
    repo, bin_dir = tmp_path / 'repo', tmp_path / 'bin'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'venv' / 'bin').mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy(_SCRIPTS / script, repo / 'scripts' / script)
    stubs = {repo / 'venv' / 'bin' / 'python': _FAKE_PY,
             bin_dir / 'git': '#!/usr/bin/env bash\nexit 0\n',
             bin_dir / 'claude': '#!/usr/bin/env bash\nexit 0\n'}
    for path, body in stubs.items():
        path.write_text(body, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return repo, bin_dir


def _run_wrapper(repo, bin_dir, script, *args):
    env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'}
    assert os.access(repo / 'venv' / 'bin' / 'python', os.X_OK)   # 대역이 아니면 진짜 python으로 넘어간다
    proc = subprocess.run(['bash', str(repo / 'scripts' / script), *args], env=env,
                          capture_output=True, text=True, timeout=30)
    log = repo / 'py_calls.log'
    return proc, (log.read_text(encoding='utf-8').splitlines() if log.exists() else [])


def test_테마_갱신_셸_무인자_기본은_미보유_종목만(tmp_path):
    """무인자 = --stale-days 7이라 캐시 1417/1417종목이 전부 재분류 대상(LLM WebSearch 1400회+)이었다.
    기본은 미보유 종목만, 재분류는 --all·--stale-days를 명시할 때만."""
    repo, bin_dir = _fake_repo(tmp_path, 'refresh_sectors.sh')
    proc, calls = _run_wrapper(repo, bin_dir, 'refresh_sectors.sh')
    assert proc.returncode == 0, proc.stderr
    assert [c.split(' args=')[1] for c in calls] == ['scripts/refresh_sectors.py']


@pytest.mark.parametrize('script, lock', [('refresh_sectors.sh', '.refresh_sectors.lock'),
                                          ('refresh_yf_sectors.sh', '.refresh_yf_sectors.lock')])
def test_셸_락은_mkdir로_원자적으로_잡고_끝나면_푼다(tmp_path, script, lock):
    """[ -e ] 확인 뒤 touch는 확인과 생성 사이에 틈이 있어 동시에 둘이 들어올 수 있다."""
    repo, bin_dir = _fake_repo(tmp_path, script)
    proc, calls = _run_wrapper(repo, bin_dir, script)
    assert proc.returncode == 0, proc.stderr
    assert calls and all(c.startswith('lock=dir ') for c in calls)
    assert not (repo / lock).exists()


@pytest.mark.parametrize('script, lock', [('refresh_sectors.sh', '.refresh_sectors.lock'),
                                          ('refresh_yf_sectors.sh', '.refresh_yf_sectors.lock')])
def test_셸_락이_잡혀_있으면_실행하지_않고_락을_건드리지_않는다(tmp_path, script, lock):
    repo, bin_dir = _fake_repo(tmp_path, script)
    (repo / lock).mkdir()
    proc, calls = _run_wrapper(repo, bin_dir, script)
    assert proc.returncode == 1
    assert calls == []
    assert (repo / lock).is_dir()          # 남의 락을 지우지 않는다


def test_yf_갱신_락파일은_git_추적에서_빠진다():
    lines = (Path(__file__).parent.parent / '.gitignore').read_text(encoding='utf-8').splitlines()
    assert '.refresh_yf_sectors.lock' in lines
