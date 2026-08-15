import json
import data.sector as sector
from data import theme_classifier


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
