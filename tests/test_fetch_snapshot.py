from scripts import fetch_snapshot as fs


def test_run_market_gate_blocks_save(monkeypatch):
    """성공률 70% 미만이면 저장하지 않고 False 반환 (이전 스냅샷 유지)."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A', 'B', 'C', 'D'])
    bad = {'market': 'US', 'ticker_count': 1, 'failed': ['B', 'C', 'D'], 'data': {}}
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: bad)
    saved = []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(m))
    assert fs.run_market('US') is False
    assert saved == []


def test_run_market_gate_passes(monkeypatch):
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A', 'B', 'C', 'D'])
    good = {'market': 'US', 'ticker_count': 4, 'failed': [], 'data': {}}
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: good)
    saved = []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(m))
    assert fs.run_market('US') is True
    assert saved == ['US']


def test_run_market_no_ticker_file(monkeypatch):
    monkeypatch.setattr(fs, 'load_tickers', lambda m: [])
    assert fs.run_market('US') is True   # 티커 없음은 실패가 아니다


def test_run_market_rate_ignores_duplicate_lines(monkeypatch):
    """성공률 분모는 실제 시도 수(성공+실패) — 티커 파일 중복 라인에 영향받지 않는다."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'] * 300 + ['B'] * 90)
    # 유니크 2개 중 2개 성공 → 100% (기존 계산이면 2/390=0.5%로 오판)
    good = {'market': 'US', 'ticker_count': 2, 'failed': [], 'data': {}}
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: good)
    saved = []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(m))
    assert fs.run_market('US') is True
    assert saved == ['US']


def test_run_market_retries_when_stocks_lag_index(monkeypatch):
    """종목 마지막 거래일이 시장 지수보다 늦으면 기다렸다 다시 받는다 (2026-11-10 US 사례)."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    monkeypatch.setattr(fs, '_index_last', lambda m: '2026-11-09')
    snaps = iter([{'ticker_count': 1, 'failed': [], 'data': {}, 'last_trading_date': d}
                  for d in ('2026-11-06', '2026-11-06', '2026-11-09')])
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: next(snaps))
    saved, slept = [], []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(s['last_trading_date']))
    assert fs.run_market('US', waits=(1, 2), sleep=slept.append) is True
    assert slept == [1, 2] and saved == ['2026-11-09']


def test_run_market_no_retry_when_up_to_date(monkeypatch):
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    monkeypatch.setattr(fs, '_index_last', lambda m: '2026-11-09')
    monkeypatch.setattr(fs.store, 'build_market_snapshot',
                        lambda *a, **k: {'ticker_count': 1, 'failed': [], 'data': {}, 'last_trading_date': '2026-11-09'})
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: None)
    slept = []
    assert fs.run_market('US', waits=(1, 2), sleep=slept.append) is True
    assert slept == []


def _stock_snap(last):
    return {'ticker_count': 1, 'failed': [], 'data': {}, 'last_trading_date': last}


def test_run_market_refetches_index_when_index_lags_stocks(monkeypatch):
    """#24: 종목 11-11, NASDAQ 11-10 — 지수가 늦으면 기다렸다 지수를 다시 받는다(종목은 다시 안 받는다)."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    index_last = ['2026-11-10']
    monkeypatch.setattr(fs, '_index_last', lambda m: index_last[0])
    builds = []
    monkeypatch.setattr(fs.store, 'build_market_snapshot',
                        lambda *a, **k: (builds.append(1), _stock_snap('2026-11-11'))[1])
    refetched = []

    def refetch(markets):
        refetched.append(markets)
        index_last[0] = '2026-11-11'
    monkeypatch.setattr(fs.store, 'refetch_indices', refetch)
    saved, slept = [], []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(s['last_trading_date']))

    assert fs.run_market('US', waits=(1, 2), sleep=slept.append) is True
    assert slept == [1] and refetched == ['us'] and len(builds) == 1
    assert saved == ['2026-11-11']


def test_run_market_index_lag_refetches_own_market_indices(monkeypatch):
    """KR 시장의 지수 지연은 KR 지수만 다시 받는다 — NASDAQ까지 받을 이유가 없다."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    index_last = ['2026-11-11']
    monkeypatch.setattr(fs, '_index_last', lambda m: index_last[0])
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: _stock_snap('2026-11-12'))
    refetched = []

    def refetch(markets):
        refetched.append(markets)
        index_last[0] = '2026-11-12'
    monkeypatch.setattr(fs.store, 'refetch_indices', refetch)
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: None)

    assert fs.run_market('KR_KOSDAQ', waits=(0,), sleep=lambda s: None) is True
    assert refetched == ['kr']


def test_run_market_saves_with_warning_when_index_still_lags(monkeypatch, capsys):
    """재시도해도 지수가 늦으면 종목은 저장하고 경고만 — 폴백 게이트(check_snapshot_fresh)가 이어받는다."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    monkeypatch.setattr(fs, '_index_last', lambda m: '2026-11-10')
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: _stock_snap('2026-11-11'))
    refetched = []
    monkeypatch.setattr(fs.store, 'refetch_indices', refetched.append)
    saved, slept = [], []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(m))

    assert fs.run_market('US', waits=(1, 2), sleep=slept.append) is True
    assert slept == [1, 2] and refetched == ['us', 'us']
    assert saved == ['US']
    assert '2026-11-10' in capsys.readouterr().out


# ── 지수 수집 실패 → 종료 코드 3 (종목은 저장, 배치는 실패로 끝남) ──────────

def _main_with(monkeypatch, index_failed, market_ok=True):
    monkeypatch.setattr('sys.argv', ['fetch_snapshot.py', '--markets', 'us'])
    monkeypatch.setattr(fs.store, 'refetch_indices',
                        lambda m: {'failed': index_failed, 'data': {}})
    monkeypatch.setattr(fs, 'run_market', lambda m, **k: market_ok)
    return fs.main()


def test_main_returns_index_failed_code_when_index_failed(monkeypatch, capsys):
    assert _main_with(monkeypatch, ['NASDAQ']) == fs.INDEX_FAILED_EXIT == 3
    # run_batch_local.sh가 이 문구에서 실패한 지수명을 가져간다
    assert '지수 수집 실패: NASDAQ' in capsys.readouterr().out


def test_main_market_failure_wins_over_index_failure(monkeypatch):
    assert _main_with(monkeypatch, ['NASDAQ'], market_ok=False) == 1


def test_main_success(monkeypatch):
    assert _main_with(monkeypatch, []) == 0


# ── 두 기능의 연결: 첫 지수 수집 실패(#47 종료 코드 3) ↔ 지수 지연 재수집(#24) ──────────

def test_run_market_index_refetch_success_clears_index_failure(monkeypatch):
    """첫 수집에서 NASDAQ이 실패해 하루 늦게 남았어도 재수집에 성공하면 실패 목록에서 빠진다."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    index_last = ['2026-11-10']
    monkeypatch.setattr(fs, '_index_last', lambda m: index_last[0])
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: _stock_snap('2026-11-11'))

    def refetch(markets):
        index_last[0] = '2026-11-11'
        return {'failed': []}
    monkeypatch.setattr(fs.store, 'refetch_indices', refetch)
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: None)
    index_failed = {'NASDAQ'}

    assert fs.run_market('US', waits=(0,), sleep=lambda s: None, index_failed=index_failed) is True
    assert index_failed == set()


def test_run_market_index_refetch_failure_keeps_index_failure(monkeypatch):
    """재수집도 실패하면 그 지수는 실패로 남는다. 이미 받은 다른 지수를 새로 실패로 올리지 않는다."""
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    monkeypatch.setattr(fs, '_index_last', lambda m: '2026-11-11')
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: _stock_snap('2026-11-12'))
    monkeypatch.setattr(fs.store, 'refetch_indices', lambda m: {'failed': ['KOSPI', 'KOSDAQ']})
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: None)
    index_failed = {'KOSDAQ'}     # KOSPI는 첫 수집에서 받았다

    assert fs.run_market('KR_KOSDAQ', waits=(0,), sleep=lambda s: None, index_failed=index_failed) is True
    assert index_failed == {'KOSDAQ'}


def test_main_index_failure_recovered_by_retry_is_success(monkeypatch, capsys):
    """첫 지수 수집은 실패했지만 run_market의 재수집이 받아내면 종료 코드 0 — 배치 실패가 아니다."""
    monkeypatch.setattr('sys.argv', ['fetch_snapshot.py', '--markets', 'us'])
    monkeypatch.setattr(fs.store, 'refetch_indices', lambda m: {'failed': ['NASDAQ'], 'data': {}})

    def run_market(m, index_failed):
        index_failed.discard('NASDAQ')
        return True
    monkeypatch.setattr(fs, 'run_market', run_market)

    assert fs.main() == 0
    assert '지수 수집 실패: NASDAQ' not in capsys.readouterr().out   # run_batch_local.sh가 실패로 집지 않는다
