import json
import data.sector as sector


def _raise(msg):
    raise AssertionError(msg)


def _setup(tmp_path, monkeypatch, cache_content: dict):
    cache_file = tmp_path / 'sector_cache.json'
    cache_file.write_text(json.dumps(cache_content), encoding='utf-8')
    monkeypatch.setattr(sector, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(sector, '_SESSION_ATTEMPTED', set(), raising=False)
    monkeypatch.setattr(sector, '_build_summary', lambda t, m: 'dummy')
    return cache_file


def test_gita_result_is_not_persisted(tmp_path, monkeypatch):
    """일시적 실패로 나온 '기타'는 디스크 캐시에 저장하지 않는다 — 재시작 시 재분류 기회."""
    cache_file = _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(sector, '_classify', lambda s: _raise('CLI 없음'))
    monkeypatch.setattr(sector, '_fallback_sector', lambda t, m: '기타')

    result = sector.get_sectors(['005930'], 'KR_KOSPI')

    assert result == {'005930': '기타'}
    assert '005930|KR_KOSPI' not in json.loads(cache_file.read_text(encoding='utf-8'))


def test_gita_not_retried_within_session(tmp_path, monkeypatch):
    """같은 프로세스 안에서는 '기타' 종목을 반복 재분류(60초 타임아웃)하지 않는다."""
    _setup(tmp_path, monkeypatch, {})
    calls = []
    monkeypatch.setattr(sector, '_classify', lambda s: calls.append(1) and _raise('CLI 없음'))
    monkeypatch.setattr(sector, '_fallback_sector', lambda t, m: '기타')

    sector.get_sectors(['005930'], 'KR_KOSPI')
    sector.get_sectors(['005930'], 'KR_KOSPI')

    assert len(calls) == 1


def test_legacy_cached_gita_retried_and_upgraded(tmp_path, monkeypatch):
    """과거에 '기타'로 저장된 종목은 세션당 1회 재분류를 시도하고, 성공하면 승격 저장한다."""
    cache_file = _setup(tmp_path, monkeypatch, {'005930|KR_KOSPI': '기타'})
    monkeypatch.setattr(sector, '_classify', lambda s: '반도체 소재·부품')

    result = sector.get_sectors(['005930'], 'KR_KOSPI')

    assert result == {'005930': '반도체 소재·부품'}
    assert json.loads(cache_file.read_text(encoding='utf-8'))['005930|KR_KOSPI'] == '반도체 소재·부품'


def test_cached_only_lookup_never_classifies(tmp_path, monkeypatch):
    """as-of 빌드용 캐시 전용 조회 — 미캐시 종목이어도 분류(subprocess/네트워크)를 트리거하지 않는다."""
    _setup(tmp_path, monkeypatch, {'005930|KR_KOSPI': '반도체 소재·부품'})
    monkeypatch.setattr(sector, '_classify', lambda s: _raise('_classify 호출됨'))
    monkeypatch.setattr(sector, '_fallback_sector', lambda t, m: _raise('_fallback 호출됨'))

    result = sector.get_sectors_cached_only(['005930', '000660'], 'KR_KOSPI')

    assert result == {'005930': '반도체 소재·부품', '000660': '기타'}
