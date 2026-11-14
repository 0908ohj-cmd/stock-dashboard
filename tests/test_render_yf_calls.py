"""#39 M14 (data 계층) — 렌더 경로의 불필요한 yfinance `.info` 호출.

- 승인 분류가 있는 종목도 get_yf_sectors(.info)를 먼저 불러 결과를 버렸다 (실측 조회 20건·사용 0건)
- US 종목명을 종목마다 .info로 조회하고 실패를 캐시하지 않았다. classification/us.json의
  companyName은 쓰지 않았다
"""
import json

import pytest

import data.sector as sector
import data.yf_sector as yf_sector
from data import classification, fetcher, theme_classifier


@pytest.fixture
def verified_store(tmp_path, monkeypatch):
    """AAOI만 승인 분류 기록이 있는 저장소. 구 캐시는 비운다."""
    monkeypatch.setattr(classification, 'STORE_DIR', tmp_path / 'cls')
    classification._cache.clear()
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(sector, '_LOGGED', set())
    recs = {}
    classification.apply_auto('US', 'AAOI', classification.normalize('US', {
        'companyName': 'Applied Optoelectronics, Inc.',
        'primaryTheme': 'OPTICAL_INTERCONNECT', 'confidence': 'high',
        'evidenceUrls': ['https://stockanalysis.com/stocks/aaoi/company/']}, 't'), recs)
    classification.save_store('US', recs)
    yield
    classification._cache.clear()


def _spy_yf_sectors(monkeypatch, labels):
    calls = []

    def _fake(tickers, market):
        calls.append(list(tickers))
        return {t: labels.get(t, '-') for t in tickers}
    monkeypatch.setattr(yf_sector, 'get_yf_sectors', _fake)
    return calls


def test_승인_분류가_있는_종목은_yfinance_섹터를_조회하지_않는다(verified_store, monkeypatch):
    calls = _spy_yf_sectors(monkeypatch, {'NEWCO': '반도체'})

    out = sector.get_display_sectors(['AAOI', 'NEWCO'], 'US')

    assert calls == [['NEWCO']]
    assert out['NEWCO'] == '반도체'
    assert out['AAOI'] == classification.display_major('US', 'AAOI')


def test_전부_승인_분류면_yfinance를_부르지_않는다(verified_store, monkeypatch):
    calls = _spy_yf_sectors(monkeypatch, {})
    sector.get_display_sectors(['AAOI'], 'US')
    assert calls == []


# ── US 종목명 ─────────────────────────────────────────────

@pytest.fixture
def name_env(monkeypatch):
    monkeypatch.setattr(fetcher, '_name_cache', {})
    monkeypatch.setattr(fetcher, '_name_fail_until', {}, raising=False)
    now = [1000.0]
    monkeypatch.setattr(fetcher, '_clock', lambda: now[0])
    info_calls = []

    class _Ticker:
        def __init__(self, sym):
            info_calls.append(sym)

        @property
        def info(self):
            raise RuntimeError('Too Many Requests')
    monkeypatch.setattr(fetcher.yf, 'Ticker', _Ticker)
    return now, info_calls


def test_US_종목명은_분류_저장소의_companyName을_먼저_쓴다(verified_store, name_env):
    _, info_calls = name_env
    assert fetcher.get_stock_name('AAOI', 'US') == 'Applied Optoelectronics, Inc.'
    assert info_calls == []


def test_US_종목명_조회_실패는_TTL_동안_다시_조회하지_않는다(verified_store, name_env):
    now, info_calls = name_env
    assert fetcher.get_stock_name('ZZZZ', 'US') == 'ZZZZ'
    assert fetcher.get_stock_name('ZZZZ', 'US') == 'ZZZZ'
    assert info_calls == ['ZZZZ']                      # 두 번째는 음성 캐시

    now[0] += fetcher._NAME_FAIL_TTL_SEC + 1
    assert fetcher.get_stock_name('ZZZZ', 'US') == 'ZZZZ'
    assert info_calls == ['ZZZZ', 'ZZZZ']              # TTL이 지나면 다시 시도
