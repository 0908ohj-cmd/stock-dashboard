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
