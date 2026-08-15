import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    'refresh_sectors', Path(__file__).parent.parent / 'scripts' / 'refresh_sectors.py')
refresh_sectors = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(refresh_sectors)

from data import theme_classifier


def test_select_targets_default_uncached_only():
    """기본 모드: 캐시에 없는(또는 theme/detail 둘 다 빈) 종목만 대상."""
    cache = {
        'AAA': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2020-01-01T00:00:00'},
        'BBB': {'theme': '', 'detail': '', 'updated': '2026-01-01T00:00:00'},
    }
    out = refresh_sectors._select_targets(['AAA', 'BBB', 'CCC'], cache,
                                          all_mode=False, stale_days=None)
    assert out == ['BBB', 'CCC']


def test_select_targets_stale_days():
    """--stale-days N: 미캐시 + updated가 N일 이전인 종목."""
    old = (datetime.now() - timedelta(days=10)).isoformat()
    new = datetime.now().isoformat()
    cache = {
        'OLD': {'theme': '기타', 'detail': 'x', 'updated': old},
        'NEW': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': new},
    }
    out = refresh_sectors._select_targets(['OLD', 'NEW', 'MISS'], cache,
                                          all_mode=False, stale_days=7)
    assert out == ['OLD', 'MISS']


def test_select_targets_all_mode():
    cache = {'AAA': {'theme': '메모리 반도체', 'detail': 'HBM', 'updated': '2026-01-01T00:00:00'}}
    out = refresh_sectors._select_targets(['AAA', 'BBB'], cache, all_mode=True, stale_days=None)
    assert out == ['AAA', 'BBB']


def test_run_saves_cache_once_and_resolves_misc(tmp_path, monkeypatch):
    """병렬 분류 → 성공분 캐시 반영, '기타'+detail은 resolve 배치, 저장은 마지막 1회."""
    cache_file = tmp_path / 'theme_cache.json'
    cache_file.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(theme_classifier, '_CACHE_FILE', cache_file)
    saves = []
    real_save = theme_classifier._save_cache
    monkeypatch.setattr(theme_classifier, '_save_cache',
                        lambda c: (saves.append(1), real_save(c)))

    def fake_classify_one(ticker, name_hint, themes):
        return {'AAA': {'theme': '메모리 반도체', 'detail': 'HBM'},
                'MSC': {'theme': '기타', 'detail': '신규 테마 후보'},
                'BAD': None}[ticker]

    monkeypatch.setattr(theme_classifier, '_classify_one', fake_classify_one)
    monkeypatch.setattr(theme_classifier, '_resolve_misc',
                        lambda tickers, cur, themes: {'MSC': {'theme': '신테마', 'detail': '신규 테마 후보'}})

    themes = [{'name': '메모리 반도체', 'description': '...'}]
    failed = refresh_sectors._classify_targets(['AAA', 'MSC', 'BAD'], themes, {}, workers=2)

    saved = json.loads(cache_file.read_text(encoding='utf-8'))
    assert saved['AAA']['detail'] == 'HBM'
    assert saved['MSC']['theme'] == '신테마'
    assert 'BAD' not in saved          # 완전 실패는 미저장 (자가치유)
    assert failed == ['BAD']
    assert len(saves) == 1             # 저장은 마지막 1회
