import sys
import types

import pytest

from scripts import build_us_growth_universe as bu


def _write(path, n, prefix='OLD'):
    path.write_text('\n'.join(f'{prefix}{i}' for i in range(n)), encoding='utf-8')


def _lines(path):
    return [t for t in path.read_text(encoding='utf-8').splitlines() if t.strip()]


@pytest.fixture
def save_path(tmp_path, monkeypatch):
    """실제 data/saved/us_10ema.tickers를 건드리지 않게 저장 경로를 tmp로 돌린다."""
    p = tmp_path / 'us_10ema.tickers'
    monkeypatch.setattr(bu, 'SAVE_PATH', p)
    return p


def test_shrunk_list_keeps_existing_file_and_fails(save_path, monkeypatch):
    """#38: 부분 수집으로 21개만 남아도(890c485: 정상 119 → 21) 기존 203개를 덮지 않고 1로 알린다."""
    _write(save_path, 203)
    before = save_path.read_text(encoding='utf-8')
    monkeypatch.setattr(bu, 'build', lambda: [f'NEW{i}' for i in range(21)])

    assert bu.main() == 1
    assert save_path.read_text(encoding='utf-8') == before


def test_normal_size_list_replaces_existing_file(save_path, monkeypatch):
    _write(save_path, 203)
    new = [f'NEW{i}' for i in range(195)]
    monkeypatch.setattr(bu, 'build', lambda: new)

    assert bu.main() == 0
    assert _lines(save_path) == new


@pytest.mark.parametrize('n_new, rc', [(60, 0), (59, 1)])
def test_guard_threshold_is_ratio_of_existing(save_path, monkeypatch, n_new, rc):
    """기존 100개 기준 60%(MIN_KEEP_RATIO) 이상이면 저장, 미만이면 유지."""
    _write(save_path, 100)
    monkeypatch.setattr(bu, 'build', lambda: [f'NEW{i}' for i in range(n_new)])

    assert bu.main() == rc
    assert len(_lines(save_path)) == (n_new if rc == 0 else 100)


def test_first_run_saves_without_baseline(save_path, monkeypatch):
    """기존 파일이 없으면 비교 기준이 없으므로 받은 목록을 그대로 저장한다."""
    monkeypatch.setattr(bu, 'build', lambda: ['AAA', 'BBB'])

    assert bu.main() == 0
    assert _lines(save_path) == ['AAA', 'BBB']


def test_fdr_failure_does_not_save_hardcoded_list(save_path, monkeypatch):
    """#38: FDR이 죽으면 하드코딩 후보로 유니버스를 만들지 않는다 — 최초 실행이어도 저장 안 함."""
    fake = types.ModuleType('FinanceDataReader')

    def boom(*a, **k):
        raise RuntimeError('HTTP Error 404: Not Found')

    fake.StockListing = boom
    monkeypatch.setitem(sys.modules, 'FinanceDataReader', fake)
    monkeypatch.setattr(bu, '_filter_by_technicals', lambda syms: list(syms))   # yfinance 차단

    assert bu.main() == 1
    assert not save_path.exists()
