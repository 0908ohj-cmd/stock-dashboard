import json

import pytest

import data.sector as sector
from data import classification, theme_classifier


@pytest.fixture(autouse=True)
def _empty_verified_store(tmp_path_factory, monkeypatch):
    """실제 data/classification/*.json이 구 캐시 경로 테스트에 끼어들지 않게 빈 저장소로 격리."""
    monkeypatch.setattr(classification, 'STORE_DIR', tmp_path_factory.mktemp('cls'))
    classification._cache.clear()


def test_검증된_새_분류가_구_캐시보다_우선한다(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        '000660': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2020-01-01T00:00:00'},
        '005930': {'theme': '메모리 반도체', 'detail': '구 라벨', 'updated': '2020-01-01T00:00:00'},
    })
    recs = {}
    classification.apply_auto('KR_KOSPI', '000660', classification.normalize('KR_KOSPI', {
        'primaryTheme': 'SEMI_MEMORY', 'confidence': 'high', 'detail': 'HBM 메모리',
        'evidenceUrls': ['https://dart.fss.or.kr/x']}, 't'), recs)
    classification.apply_auto('KR_KOSPI', '005930', classification.normalize('KR_KOSPI', {
        'primaryTheme': 'SEMI_MEMORY', 'confidence': 'low', 'evidenceUrls': []}, 't'), recs)
    classification.save_store('KR_KOSPI', recs)
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))
    # 005930은 승인 게이트(근거 URL·신뢰도)에서 탈락해 기록이 미분류다. 주도섹터 탭이 집계에서 빼는
    # 종목이므로 와치리스트도 '미분류'로 보여준다 — 동결된 구 캐시 라벨로 폴백하지 않는다(#37)
    assert sector.get_sectors(['000660', '005930'], 'KR_KOSPI') == {'000660': 'HBM 메모리', '005930': '미분류'}
    assert sector.get_major_themes(['000660', '005930'], 'KR_KOSPI') == {
        '000660': '반도체·메모리', '005930': '미분류'}


AAOI_LEGACY = {'AAOI': {'theme': '광통신', 'detail': 'AI 데이터센터 광부품', 'updated': '2026-08-15T22:43:12'}}


def _no_classify(monkeypatch):
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))


def test_미분류로_확정한_종목은_구_라벨로_폴백하지_않는다(tmp_path, monkeypatch):
    """AAOI를 '미분류'로 확정해도 와치리스트에 '광통신 / AI 데이터센터 광부품'이 나오던 문제(#37)."""
    _setup(tmp_path, monkeypatch, AAOI_LEGACY)
    _no_classify(monkeypatch)
    recs = {}
    classification.apply_auto('US', 'AAOI', classification.normalize('US', {
        'primaryTheme': 'OPTICAL_INTERCONNECT', 'confidence': 'medium', 'detail': '광트랜시버·CATV 광장비',
        'evidenceUrls': ['https://stockanalysis.com/stocks/aaoi/company/']}, 't'), recs)
    classification.set_override('US', 'AAOI', 'UNCLASSIFIED', [], '사업 비중 재확인 필요', recs)
    classification.save_store('US', recs)

    assert sector.get_sectors(['AAOI'], 'US') == {'AAOI': '미분류'}
    assert sector.get_sectors_cached_only(['AAOI'], 'US') == {'AAOI': '미분류'}
    assert sector.get_major_themes(['AAOI'], 'US') == {'AAOI': '미분류'}
    assert sector.get_display_sectors(['AAOI'], 'US', {'AAOI': '통신장비'}) == {'AAOI': '미분류'}


def test_분류_기록이_없는_신규종목은_예전처럼_구_라벨로_폴백한다(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, AAOI_LEGACY)
    _no_classify(monkeypatch)

    assert sector.get_sectors(['AAOI'], 'US') == {'AAOI': 'AI 데이터센터 광부품'}
    assert sector.get_display_sectors(['AAOI'], 'US', {'AAOI': '통신장비'}) == {'AAOI': '통신장비'}
    assert sector.get_display_sectors(['AAOI'], 'US', {}) == {'AAOI': '광통신'}


def test_분류_저장소를_못_읽으면_로그를_남기고_구_라벨로_폴백한다(tmp_path, monkeypatch, caplog):
    _setup(tmp_path, monkeypatch, AAOI_LEGACY)
    _no_classify(monkeypatch)
    classification.STORE_DIR.mkdir(exist_ok=True)
    (classification.STORE_DIR / 'us.json').write_text('{깨진 JSON', encoding='utf-8')

    with caplog.at_level('WARNING', logger='data.sector'):
        labels = sector.get_sectors(['AAOI'], 'US')
        majors = sector.get_display_sectors(['AAOI'], 'US', {})

    assert labels == {'AAOI': 'AI 데이터센터 광부품'}
    assert majors == {'AAOI': '광통신'}
    assert any('AAOI' in r.getMessage() and 'JSONDecodeError' in r.getMessage() for r in caplog.records)


def test_테마_목록에_없는_코드는_로그를_남긴다(tmp_path, monkeypatch, caplog):
    """taxonomy 코드가 바뀌어 기록의 코드가 목록에 없으면 조용히 넘어가지 않는다."""
    _setup(tmp_path, monkeypatch, AAOI_LEGACY)
    _no_classify(monkeypatch)
    rec = classification.normalize('US', {
        'primaryTheme': 'OPTICAL_INTERCONNECT', 'confidence': 'high',
        'evidenceUrls': ['https://stockanalysis.com/stocks/aaoi/company/']}, 't')
    rec['primaryTheme'] = 'RENAMED_CODE'
    classification.save_store('US', {'AAOI': rec})

    with caplog.at_level('WARNING', logger='data.sector'):
        sector.get_display_sectors(['AAOI'], 'US', {})

    assert any('RENAMED_CODE' in r.getMessage() for r in caplog.records)


def _setup(tmp_path, monkeypatch, cache_content: dict):
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text(json.dumps(cache_content, ensure_ascii=False), encoding='utf-8')
    themes_file = tmp_path / 'themes.json'
    themes_file.write_text(json.dumps({
        'known_themes': [{'name': '메모리 반도체', 'description': '...'}],
        'ticker_overrides': {'KOSPI': '지수'},
    }, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(theme_classifier, '_THEMES_FILE', themes_file)
    monkeypatch.setattr(sector, '_SESSION_ATTEMPTED', set())
    monkeypatch.setattr(sector, '_LOGGED', set())
    return cache_file


def test_cache_hit_returns_detail_ttl_ignored(tmp_path, monkeypatch):
    """캐시 보유분은 updated가 아무리 오래돼도(TTL 무시) 그대로 쓴다 — 렌더 무블로킹."""
    _setup(tmp_path, monkeypatch, {
        '000660': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2020-01-01T00:00:00'},
    })
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))
    assert sector.get_sectors(['000660'], 'KR_KOSPI') == {'000660': 'HBM'}


def test_detail_missing_falls_back_to_theme(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        '000660': {'theme': '메모리 반도체', 'detail': '', 'updated': '2026-01-01T00:00:00'},
    })
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))
    assert sector.get_sectors(['000660'], 'KR_KOSPI') == {'000660': '메모리 반도체'}


def test_only_uncached_passed_to_classify(tmp_path, monkeypatch):
    """미캐시 종목만 classify로 넘어간다."""
    _setup(tmp_path, monkeypatch, {
        '000660': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2026-01-01T00:00:00'},
    })
    calls = []

    def fake_classify(tickers, themes, overrides=None, names=None):
        calls.append(list(tickers))
        return {t: {'theme': '메모리 반도체', 'detail': 'DRAM'} for t in tickers}

    monkeypatch.setattr(theme_classifier, 'classify', fake_classify)
    result = sector.get_sectors(['000660', '005930'], 'KR_KOSPI')
    assert calls == [['005930']]
    assert result == {'000660': 'HBM', '005930': 'DRAM'}


def test_failure_shows_gita_and_not_retried_within_session(tmp_path, monkeypatch):
    """분류 실패는 '기타' 표시 + 세션 내 재시도 금지 (rerun마다 subprocess 반복 방지)."""
    _setup(tmp_path, monkeypatch, {})
    calls = []

    def fake_classify(tickers, themes, overrides=None, names=None):
        calls.append(list(tickers))
        return {t: {'theme': '기타', 'detail': ''} for t in tickers}

    monkeypatch.setattr(theme_classifier, 'classify', fake_classify)
    assert sector.get_sectors(['005930'], 'KR_KOSPI') == {'005930': '기타'}
    assert sector.get_sectors(['005930'], 'KR_KOSPI') == {'005930': '기타'}
    assert len(calls) == 1


def test_override_bypasses_classify(tmp_path, monkeypatch):
    """지수 티커 등 ticker_overrides는 LLM 호출 없이 즉시 라벨."""
    _setup(tmp_path, monkeypatch, {})
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))
    assert sector.get_sectors(['KOSPI'], 'KR_KOSPI') == {'KOSPI': '지수'}


def test_kr_names_hint_passed(tmp_path, monkeypatch):
    """KR 마켓이면 kr_names.json 번들의 종목명이 names 힌트로 전달된다."""
    _setup(tmp_path, monkeypatch, {})
    names_file = tmp_path / 'kr_names.json'
    names_file.write_text(json.dumps({'005930': '삼성전자'}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(sector, '_NAMES_FILE', names_file)
    seen = {}

    def fake_classify(tickers, themes, overrides=None, names=None):
        seen['names'] = names
        return {t: {'theme': '메모리 반도체', 'detail': 'DRAM'} for t in tickers}

    monkeypatch.setattr(theme_classifier, 'classify', fake_classify)
    sector.get_sectors(['005930'], 'KR_KOSPI')
    assert seen['names'] == {'005930': '삼성전자'}


def test_cached_only_never_classifies(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {
        '000660': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2026-01-01T00:00:00'},
    })
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))
    result = sector.get_sectors_cached_only(['000660', '005930', 'KOSPI'], 'KR_KOSPI')
    assert result == {'000660': 'HBM', '005930': '기타', 'KOSPI': '지수'}
import json
import data.sector as sector
from data import theme_classifier


def test_get_major_themes_캐시만_읽는다(tmp_path, monkeypatch):
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text(json.dumps({
        '005930': {'theme': '메모리 반도체', 'detail': 'HBM·AI 메모리', 'updated': '2026-01-01T00:00:00'},
    }, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    monkeypatch.setattr(theme_classifier, 'classify',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('classify 호출됨')))

    result = sector.get_major_themes(['005930', '없는티커'])
    assert result == {'005930': '메모리 반도체', '없는티커': ''}
