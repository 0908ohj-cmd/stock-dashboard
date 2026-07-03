import json
import data.sector as sector


def _raise(msg):
    raise AssertionError(msg)


def test_cached_gita_is_not_reclassified(tmp_path, monkeypatch):
    """캐시에 '기타'로 저장된 종목은 재분류(subprocess/yfinance) 없이 그대로 반환해야 한다."""
    cache_file = tmp_path / 'sector_cache.json'
    cache_file.write_text(json.dumps({'005930|KR_KOSPI': '기타'}), encoding='utf-8')
    monkeypatch.setattr(sector, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(sector, '_build_summary', lambda t, m: 'dummy')
    monkeypatch.setattr(sector, '_classify', lambda s: _raise('_classify가 호출됨'))
    monkeypatch.setattr(sector, '_fallback_sector', lambda t, m: _raise('_fallback_sector가 호출됨'))

    result = sector.get_sectors(['005930'], 'KR_KOSPI')

    assert result == {'005930': '기타'}
