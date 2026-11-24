"""yfinance 미확정 봉 방어 + 수집 이상 텔레그램 알림.

2026-11-23 KR 맥 배치: KRX 비밀번호 만료로 pyKRX 지수 조회가 실패해 yfinance로 넘어갔고, yfinance가
시가 0(일부 고가·저가도 0)인 임시 봉을 줘 전 종목이 INVALID_DATA → 주도주 점수 '산출 불가'가 됐다.
배치는 성공으로 끝나 아무 알림도 없었다.
"""
import http.server
import json
import pathlib
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from data import fetcher, store
from scripts import check_snapshot_fresh as cs
from scripts import fetch_snapshot as fs

ROOT = pathlib.Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
D1, D2 = '2026-11-20', '2026-11-23'
GOOD = (100.0, 110.0, 90.0, 105.0, 1000.0)
ZERO_OPEN = (0.0, 110.0, 90.0, 105.0, 900.0)
ZERO_OHL = (0.0, 0.0, 0.0, 105.0, 900.0)
HALTED = (0.0, 0.0, 0.0, 105.0, 0.0)            # 거래정지 — 거래량이 없으면 미확정 봉이 아니다


def _bars(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows.values()), index=pd.to_datetime(list(rows)),
                        columns=['Open', 'High', 'Low', 'Close', 'Volume'])


def _dates(df) -> list:
    return [d.strftime('%Y-%m-%d') for d in df.index]


@pytest.fixture(autouse=True)
def _no_real_telegram(monkeypatch):
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)


@pytest.fixture
def snap_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'OHLCV_DIR', tmp_path)
    monkeypatch.setattr(store, '_snap_cache', {})
    return tmp_path


# ── 병합: 미확정 봉은 저장하지 않는다 ──────────────────────

@pytest.mark.parametrize('bad', [ZERO_OPEN, ZERO_OHL, (100.0, 0.0, 90.0, 105.0, 900.0), (100.0, 110.0, 0.0, 105.0, 900.0)])
def test_거래량이_있는데_시가_고가_저가가_0이면_저장하지_않는다(bad):
    merged = store._merge_history(_bars({D1: GOOD, D2: bad}), None, 350)
    assert _dates(merged) == [D1]


def test_새_응답이_미확정이면_기존_정상_행을_지킨다():
    old = store._df_to_records(_bars({D1: GOOD, D2: GOOD}))
    merged = store._merge_history(_bars({D1: GOOD, D2: ZERO_OPEN}), old, 350)
    assert merged.loc[D2, 'Open'] == 100.0


def test_이미_저장된_미확정_행은_새_응답에_그날이_없어도_보존하지_않는다():
    """11-23 지수 사례: 야후 응답에서 그날이 빠지면 '결손 보존'이 시가 0 행을 계속 살려 둔다."""
    old = store._df_to_records(_bars({D1: GOOD, D2: ZERO_OPEN}))
    merged = store._merge_history(_bars({D1: GOOD}), old, 350)
    assert _dates(merged) == [D1]


def test_다음_수집의_정상_행이_미확정_행을_대체한다():
    old = store._df_to_records(_bars({D1: GOOD, D2: ZERO_OPEN}))
    merged = store._merge_history(_bars({D1: GOOD, D2: GOOD}), old, 350)
    assert merged.loc[D2, 'Open'] == 100.0


def test_거래정지_행은_그대로_둔다():
    merged = store._merge_history(_bars({D1: GOOD, D2: HALTED}), None, 350)
    assert _dates(merged) == [D1, D2]


def test_새_응답이_전부_미확정이면_기존_이력을_돌려준다():
    old = store._df_to_records(_bars({D1: GOOD}))
    merged = store._merge_history(_bars({D2: ZERO_OPEN}), old, 350)
    assert _dates(merged) == [D1]


def test_병합_실패_경로에서도_미확정_봉을_저장하지_않는다(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('병합 실패')
    monkeypatch.setattr(store, '_merge_history', boom)
    merged = store._safe_merge(_bars({D1: GOOD, D2: ZERO_OPEN}), None, 350, 'X')
    assert _dates(merged) == [D1]


# ── 스냅샷: 못 채운 날짜를 기록한다 ────────────────────────

def test_스냅샷은_못_채운_미확정_날짜를_종목별로_남긴다(snap_dir):
    def fetch(t, market, days):
        return _bars({D1: GOOD, D2: ZERO_OPEN if t == 'A' else GOOD})
    snap = store.build_market_snapshot('KR_KOSPI', ['A', 'B'], fetch_fn=fetch, throttle_sec=0)
    assert snap['provisional'] == {'A': D2}
    assert snap['data']['A']['dates'] == [D1]
    assert snap['data']['B']['dates'] == [D1, D2]


def test_기존_정상_행으로_채워졌으면_미확정으로_남기지_않는다(snap_dir):
    store.save_snapshot('KR_KOSPI', {'data': {'A': store._df_to_records(_bars({D1: GOOD, D2: GOOD}))}})
    snap = store.build_market_snapshot('KR_KOSPI', ['A'], throttle_sec=0,
                                       fetch_fn=lambda t, market, days: _bars({D1: GOOD, D2: ZERO_OPEN}))
    assert snap['provisional'] == {}


def test_지수도_미확정을_기록하고_이번에_안_받은_지수의_기록은_유지한다(snap_dir, monkeypatch):
    store.save_snapshot('indices', {'market': 'indices', 'provisional': {'NASDAQ': D1},
                                    'data': {'NASDAQ': store._df_to_records(_bars({D1: GOOD}))}})
    responses = {'KOSPI': _bars({D1: GOOD, D2: ZERO_OPEN}), 'KOSDAQ': _bars({D1: GOOD, D2: GOOD})}
    monkeypatch.setattr(store, 'fetch_index_daily', lambda name, days: responses[name])
    res = store.refetch_indices('kr')
    assert res['provisional'] == {'NASDAQ': D1, 'KOSPI': D2}
    responses['KOSPI'] = _bars({D1: GOOD, D2: GOOD})
    assert store.refetch_indices('kr')['provisional'] == {'NASDAQ': D1}


@pytest.mark.parametrize('left,total,want', [(0, 10, False), (1, 20, False), (2, 20, False), (3, 20, True), (10, 10, True)])
def test_미확정_종목이_10퍼센트를_넘으면_회차_미달이다(left, total, want):
    snap = {'data': {f'T{i}': {'dates': [D1]} for i in range(total)},
            'provisional': {f'T{i}': D2 for i in range(left)}}
    assert store.provisional_behind(snap) is want


# ── 회차 판정: 종목·지수가 같이 빠져도 잡는다 ───────────────

NOW = datetime(2026, 11, 23, 18, 0, tzinfo=KST)
FETCHED = '2026-11-23T17:32:00+09:00'


def _write_round(d, kospi_left=0, total=20, index_left=()):
    """종목·지수 모두 D1에서 끝난 회차 — 날짜 비교만으로는 서로 일치해 신선으로 보인다."""
    data = {f'T{i}': {'dates': [D1]} for i in range(total)}
    for name, left in (('KR_KOSPI.json', kospi_left), ('KR_KOSDAQ.json', 0)):
        (d / name).write_text(json.dumps({'fetched_at': FETCHED, 'last_trading_date': D1, 'data': data,
                                          'provisional': {f'T{i}': D2 for i in range(left)}}), encoding='utf-8')
    (d / 'indices.json').write_text(json.dumps({
        'data': {'KOSPI': {'dates': [D1]}, 'KOSDAQ': {'dates': [D1]}, 'NASDAQ': {'dates': [D1]}},
        'provisional': {n: D2 for n in index_left}}), encoding='utf-8')


def test_게이트_미확정이_없으면_신선(tmp_path):
    _write_round(tmp_path)
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is True


def test_게이트_종목이_많이_미확정이면_폴백(tmp_path):
    _write_round(tmp_path, kospi_left=10)
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is False


def test_게이트_소수_종목_미확정은_통과(tmp_path):
    _write_round(tmp_path, kospi_left=1)
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is True


def test_게이트_시장_지수가_미확정이면_폴백(tmp_path):
    _write_round(tmp_path, index_left=('KOSPI',))
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is False


def test_게이트_다른_시장_지수의_미확정은_무시한다(tmp_path):
    _write_round(tmp_path, index_left=('NASDAQ',))
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is True


# ── pyKRX → yfinance 대체 감지 ──────────────────────────────

def _index_path(monkeypatch):
    monkeypatch.setattr(fetcher, '_download', lambda *a, **k: _bars({D1: GOOD}))
    for name in ('_patch_kr_index_today', '_patch_kr_index_ohlc', '_patch_kr_index_volume'):
        monkeypatch.setattr(fetcher, name, lambda df, t: df)
    fetcher.pop_krx_fallbacks()


def test_KRX_계정이_있는데_pyKRX가_비면_대체를_기록한다(monkeypatch):
    _index_path(monkeypatch)
    monkeypatch.setenv('KRX_ID', 'x')
    monkeypatch.setenv('KRX_PW', 'y')
    monkeypatch.setattr(fetcher, '_fetch_kr_index_pykrx', lambda *a: pd.DataFrame())
    fetcher.fetch_index_daily('KOSPI', days=10)
    assert fetcher.pop_krx_fallbacks() == ['KOSPI']
    assert fetcher.pop_krx_fallbacks() == []


def test_KRX_계정이_없으면_대체를_실패로_보지_않는다(monkeypatch):
    """Streamlit Cloud에는 KRX 자격증명이 없다 — yfinance가 정상 경로다."""
    _index_path(monkeypatch)
    monkeypatch.delenv('KRX_ID', raising=False)
    monkeypatch.delenv('KRX_PW', raising=False)
    monkeypatch.setattr(fetcher, '_fetch_kr_index_pykrx', lambda *a: pd.DataFrame())
    fetcher.fetch_index_daily('KOSPI', days=10)
    assert fetcher.pop_krx_fallbacks() == []


def test_pyKRX_재수집이_성공하면_대체_기록을_지운다(monkeypatch):
    _index_path(monkeypatch)
    monkeypatch.setenv('KRX_ID', 'x')
    monkeypatch.setenv('KRX_PW', 'y')
    answers = iter([pd.DataFrame(), _bars({D1: GOOD})])
    monkeypatch.setattr(fetcher, '_fetch_kr_index_pykrx', lambda *a: next(answers))
    fetcher.fetch_index_daily('KOSPI', days=10)
    fetcher.fetch_index_daily('KOSPI', days=10)
    assert fetcher.pop_krx_fallbacks() == []


# ── 배치: 재수집과 보고 ─────────────────────────────────────

def _snap(left=(), failed=(), total=10, last=D1):
    return {'ticker_count': total - len(failed), 'failed': list(failed), 'last_trading_date': last,
            'data': {f'T{i}': {'dates': [last]} for i in range(total)},
            'provisional': {t: D2 for t in left}}


def _run(monkeypatch, snaps, **kw):
    it = iter(snaps)
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    monkeypatch.setattr(fs, '_index_last', lambda m: D1)
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: next(it))
    saved, slept, report = [], [], []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(s))
    assert fs.run_market('KR_KOSPI', sleep=slept.append, report=report, **kw) is True
    return saved, slept, report


def test_지수와_종목이_같이_빠져도_미확정이_많으면_재수집한다(monkeypatch):
    left = [f'T{i}' for i in range(10)]
    saved, slept, report = _run(monkeypatch, [_snap(left=left), _snap()], waits=(7,))
    assert slept == [7] and saved[0]['provisional'] == {} and report == []


def test_재수집_뒤에도_미확정이면_보고한다(monkeypatch):
    left = [f'T{i}' for i in range(10)]
    _, _, report = _run(monkeypatch, [_snap(left=left), _snap(left=left)], waits=(0,))
    assert len(report) == 1 and '미확정' in report[0] and '10종목' in report[0] and D2 in report[0]


def test_수집_실패가_3종목_이상이면_보고한다(monkeypatch):
    _, _, report = _run(monkeypatch, [_snap(failed=['A', 'B', 'C'])], waits=())
    assert len(report) == 1 and '수집 실패 3종목' in report[0]


def test_평소_수준의_수집_실패는_보고하지_않는다(monkeypatch):
    """과거 배치의 상시 실패는 최대 2종목이었다 — 매일 알림이 오면 진짜 이상이 묻힌다."""
    _, _, report = _run(monkeypatch, [_snap(failed=['A', 'B'])], waits=())
    assert report == []


def _main(monkeypatch, issues=(), krx=(), index_left=None):
    sent = []
    monkeypatch.setattr(fs, 'send_telegram', lambda text: sent.append(text) or True)
    monkeypatch.setattr(fs.store, 'refetch_indices', lambda m: {'failed': []})
    monkeypatch.setattr(fs.store, 'load_snapshot', lambda m: {'provisional': index_left or {}})
    monkeypatch.setattr(fs.fetcher, 'pop_krx_fallbacks', lambda: list(krx))

    def run_market(m, index_failed=None, report=None):
        report.extend(issues if m == 'KR_KOSPI' else [])
        return True
    monkeypatch.setattr(fs, 'run_market', run_market)
    monkeypatch.setattr(sys, 'argv', ['fetch_snapshot.py', '--markets', 'kr'])
    assert fs.main() == 0
    return sent


def test_정상_수집이면_알리지_않는다(monkeypatch):
    assert _main(monkeypatch) == []


def test_pyKRX_대체는_배치가_성공해도_텔레그램으로_알린다(monkeypatch):
    sent = _main(monkeypatch, krx=['KOSDAQ', 'KOSPI'])
    assert len(sent) == 1
    assert 'pyKRX' in sent[0] and 'KOSPI' in sent[0] and 'KOSDAQ' in sent[0] and '비밀번호' in sent[0]


def test_수집_이상은_한_메시지로_묶는다(monkeypatch):
    sent = _main(monkeypatch, issues=['KR_KOSPI: 미확정'], krx=['KOSPI'], index_left={'KOSPI': D2, 'NASDAQ': D1})
    assert len(sent) == 1
    assert 'pyKRX' in sent[0] and 'KR_KOSPI: 미확정' in sent[0] and '지수' in sent[0] and 'NASDAQ' not in sent[0]


# ── 텔레그램 발송 ───────────────────────────────────────────

class _Hook(http.server.BaseHTTPRequestHandler):
    status = 200
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length'])).decode()
        type(self).received.append((self.path, body))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b'{}')

    def log_message(self, *a):
        pass


@pytest.fixture
def telegram(monkeypatch):
    from scripts import notify_telegram as nt
    _Hook.received, _Hook.status = [], 200
    srv = http.server.HTTPServer(('127.0.0.1', 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(nt, 'API_BASE', f'http://127.0.0.1:{srv.server_port}')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'TOKEN123')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '42')
    yield nt
    srv.shutdown()
    srv.server_close()


def _form(body):
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(body).items()}


def test_텔레그램으로_맥_배치_표시와_함께_보낸다(telegram):
    assert telegram.send('수집 이상') is True
    path, body = _Hook.received[0]
    form = _form(body)
    assert path == '/botTOKEN123/sendMessage' and form['chat_id'] == '42'
    assert '수집 이상' in form['text'] and '맥' in form['text']


def test_GitHub_폴백이면_그렇게_표시한다(telegram, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    telegram.send('수집 이상')
    assert 'GitHub' in _form(_Hook.received[0][1])['text']


def test_토큰이_없으면_보내지_않는다(monkeypatch):
    from scripts import notify_telegram as nt
    monkeypatch.setattr(nt, 'API_BASE', 'http://127.0.0.1:9')
    assert nt.send('x') is False


def test_발송_실패는_배치를_죽이지_않고_토큰을_출력하지_않는다(telegram, capsys):
    _Hook.status = 500
    assert telegram.send('x') is False
    out = capsys.readouterr()
    assert 'TOKEN123' not in out.out + out.err


def test_긴_메시지는_텔레그램_한도_안으로_자른다(telegram):
    telegram.send('가' * 6000)
    assert len(_form(_Hook.received[0][1])['text']) <= 4096


def test_CLI는_발송_실패에도_0으로_끝난다():
    r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'notify_telegram.py'), '메시지'],
                       capture_output=True, text=True, timeout=20,
                       env={'PATH': '/usr/bin:/bin'})
    assert r.returncode == 0


def test_폴백_워크플로는_실패하면_텔레그램으로_알린다():
    text = (ROOT / '.github' / 'workflows' / 'fetch-data.yml').read_text(encoding='utf-8')
    assert text.count('TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}') == 2   # 수집 스텝·실패 스텝
    tail = text[text.index('if: failure()'):]
    assert 'scripts/notify_telegram.py' in tail
