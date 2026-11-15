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


# 아래 셋은 지운 theme_classifier.classify의 테스트가 지키던 정책 — 같은 정책을 지금 쓰는 곳(_classify_targets)에서 (#46)

def _cache_file(tmp_path, monkeypatch):
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    return cache_file


def test_1차_분류가_일시_실패하면_1회_재시도한다(tmp_path, monkeypatch):
    cache_file = _cache_file(tmp_path, monkeypatch)
    answers = iter([None, {'theme': '메모리 반도체', 'detail': 'HBM'}])
    calls = []
    monkeypatch.setattr(theme_classifier, '_classify_one', lambda *a: calls.append(a[0]) or next(answers))
    monkeypatch.setattr(theme_classifier, '_resolve_misc', lambda *a: {})

    failed = refresh_sectors._classify_targets(['WDC'], [], {}, workers=1, markets={'WDC': 'US'})

    assert calls == ['WDC', 'WDC'] and failed == []
    assert json.loads(cache_file.read_text(encoding='utf-8'))['WDC']['detail'] == 'HBM'


def test_1차_완전_실패는_2차_resolve로_넘기지_않는다(tmp_path, monkeypatch):
    """FROG 오분류 회귀 방지 — detail 없이 실패한 종목을 2차에 넘기면 티커 철자만 보고 대분류를 지어낸다."""
    cache_file = _cache_file(tmp_path, monkeypatch)
    monkeypatch.setattr(theme_classifier, '_classify_one', lambda *a: None)
    resolved_with = []
    monkeypatch.setattr(theme_classifier, '_resolve_misc', lambda batch, *a: resolved_with.append(batch) or {})

    failed = refresh_sectors._classify_targets(['FROG'], [], {}, workers=1, markets={'FROG': 'US'})

    assert resolved_with == [] and failed == ['FROG']
    assert json.loads(cache_file.read_text(encoding='utf-8')) == {}      # 미저장 → 다음 실행 때 재시도


def test_2차_resolve가_실패해도_1차_기타_detail로_캐시한다(tmp_path, monkeypatch):
    cache_file = _cache_file(tmp_path, monkeypatch)
    monkeypatch.setattr(theme_classifier, '_classify_one', lambda *a: {'theme': '기타', 'detail': '모호 테마'})
    monkeypatch.setattr(theme_classifier, '_resolve_misc', lambda *a: {})

    refresh_sectors._classify_targets(['XYZ'], [], {}, workers=1, markets={'XYZ': 'US'})

    saved = json.loads(cache_file.read_text(encoding='utf-8'))['XYZ']
    assert (saved['theme'], saved['detail']) == ('기타', '모호 테마')


# ── 깨진 캐시를 덮어쓰지 않는다 (#26 패턴, #46 후속) ─────────────────────────
# _load_cache는 파싱 실패를 {}로 돌려준다 — 그 위에 저장하면 1400여 종목 캐시가 이번 회차 결과만 남는다.
# 스크립트는 엄격 읽기(_read_cache, 실패 = None)로 확인하고 저장 없이 0이 아닌 값으로 끝나야 한다.

_BROKEN = '{"AAA": {"theme": "메모리 반도체", "detail": "HBM", "upd'


def _no_call(name):
    def f(*a, **k):
        raise AssertionError(f'{name} 호출됨 — 깨진 캐시에서 분류·조회로 넘어가면 안 된다')
    return f


def test_테마_갱신은_캐시가_깨져_있으면_저장하지_않고_실패로_끝난다(tmp_path, monkeypatch, capsys):
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text(_BROKEN, encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(refresh_sectors, '_collect_targets', lambda: [('AAA', 'US'), ('BBB', 'US')])
    monkeypatch.setattr(refresh_sectors, '_load_themes', lambda: ([], {}))
    monkeypatch.setattr(theme_classifier, '_classify_one', _no_call('_classify_one'))
    monkeypatch.setattr(theme_classifier, '_resolve_misc', _no_call('_resolve_misc'))
    monkeypatch.setattr(theme_classifier, '_save_cache', _no_call('_save_cache'))
    monkeypatch.setattr(sys, 'argv', ['refresh_sectors.py'])

    assert refresh_sectors.main() != 0
    assert cache_file.read_text(encoding='utf-8') == _BROKEN
    assert 'theme_cache.json' in capsys.readouterr().err


def test_테마_분류_단계도_깨진_캐시면_저장하지_않는다(tmp_path, monkeypatch):
    """main을 거치지 않고 _classify_targets를 바로 불러도 깨진 캐시를 {}로 보고 덮어쓰지 않는다."""
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text(_BROKEN, encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(theme_classifier, '_classify_one', _no_call('_classify_one'))

    with pytest.raises(refresh_sectors.CacheUnreadable):
        refresh_sectors._classify_targets(['AAA'], [], {}, workers=1, markets={'AAA': 'US'})
    assert cache_file.read_text(encoding='utf-8') == _BROKEN


def test_yf_갱신은_캐시가_깨져_있으면_저장하지_않고_실패로_끝난다(tmp_path, monkeypatch, capsys):
    from data import yf_sector
    cache_file = tmp_path / 'yf_sector_cache.json'
    cache_file.write_text('{"AAA": "반도', encoding='utf-8')
    monkeypatch.setattr(yf_sector, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(refresh_yf_sectors, '_collect_targets', lambda: [('AAA', 'US'), ('BBB', 'US')])
    monkeypatch.setattr(yf_sector, 'fetch_label', _no_call('fetch_label'))
    monkeypatch.setattr(yf_sector, '_save_cache', _no_call('_save_cache'))
    monkeypatch.setattr(sys, 'argv', ['refresh_yf_sectors.py'])

    assert refresh_yf_sectors.main() != 0
    assert cache_file.read_text(encoding='utf-8') == '{"AAA": "반도'
    assert 'yf_sector_cache.json' in capsys.readouterr().err


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


# 지수 심볼(KOSDAQ·KOSPI)은 store.snapshot_tickers가 거른다(#39 L7) — 갱신 대상에도 들어오지 않는다.
# QQQ는 ETF라 지수 심볼이 아니다.
_EXPECTED_PAIRS = [('0015S0', 'KR_KOSDAQ'), ('005930', 'KR_KOSPI'), ('247540', 'KR_KOSDAQ'),
                   ('NVDA', 'US'), ('QQQ', 'US')]


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


def _fake_repo(tmp_path, script, py=_FAKE_PY):
    repo, bin_dir = tmp_path / 'repo', tmp_path / 'bin'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'venv' / 'bin').mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy(_SCRIPTS / script, repo / 'scripts' / script)
    stubs = {repo / 'venv' / 'bin' / 'python': py,
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


# 수집 스크립트가 깨진 캐시로 중단(0이 아닌 종료)하면 래퍼도 실패로 끝나야 한다 — yf 래퍼는 출력만 보고
# '조회 실패 N종목'이 없으면 '[done] 실패 종목 없음'(종료 0)으로 처리했다
_FAKE_PY_FAILS = _FAKE_PY.replace('echo "수집할 종목 없음"\n',
                                  'echo "⛔ 캐시 파일을 읽지 못해 중단" >&2\nexit 2\n')


@pytest.mark.parametrize('script', ['refresh_sectors.sh', 'refresh_yf_sectors.sh'])
def test_셸은_갱신_스크립트가_실패하면_실패로_끝난다(tmp_path, script):
    assert _FAKE_PY_FAILS != _FAKE_PY
    repo, bin_dir = _fake_repo(tmp_path, script, py=_FAKE_PY_FAILS)
    proc, calls = _run_wrapper(repo, bin_dir, script)
    assert proc.returncode != 0
    assert '[done]' not in proc.stdout and '완료' not in proc.stdout
    assert len([c for c in calls if 'args=-c' not in c]) == 1      # 재시도 라운드로 넘어가지 않는다
