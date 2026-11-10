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
