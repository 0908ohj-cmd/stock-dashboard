import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))

import sync_leaderboard as sync


US_SOURCE = {
    'updated_at': '2026-07-29T07:02:16',
    'items': [
        {
            'ticker': 'DELL', 'sources': ['leaders', 'leader_ride'],
            'added_at': '2026-07-03', 'last_seen': '2026-07-29',
            'close': 426.91, 'perf_1m': 4.4, 'perf_3m': 97.9,
            'perf_6m': 271.6, 'perf_12m': 236.9, 'rs_rating': 96,
            'weighted_perf': 0.5563, 'dist_from_52w_high': -8.2,
            'dist_from_52w_low': 286.2, 'dollar_vol_w': 18879041930,
            'adr': 7.4, 'base_len_argmax': 38, 'theme': 'AI 서버 인프라',
        },
    ],
}

KR_SOURCE = {
    'updated_at': '2026-07-29T16:36:40',
    'items': [
        {
            'rank': 1, 'ticker': '005930', 'name': '삼성전자',
            'market': 'KOSPI', 'sources': ['leaders'], 'sector': '반도체',
            'close': 71000, 'market_cap': 420000000000000,
            'avg_dollar_vol': 850000000000, 'adr': 2.4,
            'dist_from_52w_high': -5.1, 'dist_from_52w_low': 42.0,
            'perf_1m': 3.2, 'perf_3m': 18.0, 'perf_6m': 30.5,
            'perf_12m': 41.2, 'rs_rating': 88, 'theme': '반도체',
        },
    ],
}


def test_normalize_us_주간거래대금을_일평균으로_환산():
    items = sync.normalize_us(US_SOURCE)
    assert items[0]['avg_dollar_vol'] == round(18879041930 / 5)


def test_normalize_us_이름은_빈문자열_섹터는_null():
    items = sync.normalize_us(US_SOURCE)
    assert items[0]['name'] == ''
    assert items[0]['sector'] is None
    assert items[0]['market'] == 'US'


def test_normalize_us_필드_보존():
    items = sync.normalize_us(US_SOURCE)
    it = items[0]
    assert it['ticker'] == 'DELL'
    assert it['rs_rating'] == 96
    assert it['adr'] == 7.4
    assert it['added_at'] == '2026-07-03'
    assert it['theme'] == 'AI 서버 인프라'
    assert it['sources'] == ['leaders', 'leader_ride']


def test_normalize_kr_이름_섹터_유지_added_at은_null():
    items = sync.normalize_kr(KR_SOURCE)
    it = items[0]
    assert it['name'] == '삼성전자'
    assert it['sector'] == '반도체'
    assert it['market'] == 'KOSPI'
    assert it['added_at'] is None


def test_normalize_kr_거래대금은_그대로():
    items = sync.normalize_kr(KR_SOURCE)
    assert items[0]['avg_dollar_vol'] == 850000000000


def test_양_시장_키_구성이_동일():
    us_keys = set(sync.normalize_us(US_SOURCE)[0].keys())
    kr_keys = set(sync.normalize_kr(KR_SOURCE)[0].keys())
    assert us_keys == kr_keys == set(sync.COMMON_KEYS)


def test_sort_and_rank_leaders_우선_그다음_RS내림차순():
    items = [
        {'ticker': 'A', 'sources': ['leader_ride'], 'rs_rating': 99, 'avg_dollar_vol': 100},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 85, 'avg_dollar_vol': 100},
        {'ticker': 'C', 'sources': ['leaders'], 'rs_rating': 92, 'avg_dollar_vol': 100},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['C', 'B', 'A']
    assert [it['rank'] for it in ranked] == [1, 2, 3]


def test_sort_and_rank_RS동률이면_거래대금_큰순():
    items = [
        {'ticker': 'A', 'sources': ['leaders'], 'rs_rating': 90, 'avg_dollar_vol': 100},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 90, 'avg_dollar_vol': 500},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['B', 'A']


def test_sort_and_rank_null값은_뒤로():
    items = [
        {'ticker': 'A', 'sources': ['leaders'], 'rs_rating': None, 'avg_dollar_vol': None},
        {'ticker': 'B', 'sources': ['leaders'], 'rs_rating': 70, 'avg_dollar_vol': 10},
    ]
    ranked = sync.sort_and_rank(items)
    assert [it['ticker'] for it in ranked] == ['B', 'A']


def test_kr_소스의_기존_rank는_무시하고_재부여():
    payload = {'updated_at': 'x', 'items': [
        dict(KR_SOURCE['items'][0], rank=9, ticker='005930', rs_rating=80),
        dict(KR_SOURCE['items'][0], rank=1, ticker='000660', rs_rating=95),
    ]}
    ranked = sync.sort_and_rank(sync.normalize_kr(payload))
    assert ranked[0]['ticker'] == '000660'
    assert ranked[0]['rank'] == 1


def test_build_envelope():
    items = sync.sort_and_rank(sync.normalize_us(US_SOURCE))
    env = sync.build_envelope('us', items, US_SOURCE['updated_at'])
    assert env['market'] == 'US'
    assert env['count'] == 1
    assert env['source_updated_at'] == '2026-07-29T07:02:16'
    assert env['synced_at']
    assert env['items'][0]['ticker'] == 'DELL'


def test_build_envelope_빈_리스트도_정상():
    env = sync.build_envelope('kr', [], '2026-07-29T16:36:40')
    assert env['count'] == 0
    assert env['items'] == []


import json
import os
import pytest


def test_load_source_us(tmp_path):
    (tmp_path / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    payload = sync.load_source('us', tmp_path)
    assert payload['updated_at'] == '2026-07-29T07:02:16'


def test_load_source_kr_파일명(tmp_path):
    (tmp_path / 'leaderboard_kr.json').write_text(
        json.dumps(KR_SOURCE, ensure_ascii=False), encoding='utf-8')
    payload = sync.load_source('kr', tmp_path)
    assert payload['items'][0]['ticker'] == '005930'


def test_load_source_파일_없으면_예외(tmp_path):
    with pytest.raises(FileNotFoundError):
        sync.load_source('us', tmp_path)


def test_sync_market_local_only_파일_생성(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)

    env = sync.sync_market('us', src, local_only=True, token=None)

    written = json.loads((out / 'us.json').read_text(encoding='utf-8'))
    assert written['count'] == 1
    assert written['items'][0]['rank'] == 1
    assert env['count'] == 1


def test_sync_market_빈_items도_정상_푸시(tmp_path, monkeypatch):
    """0개는 주도주 부재 신호 — 장애가 아니므로 그대로 쓴다."""
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard_kr.json').write_text(
        json.dumps({'updated_at': '2026-07-29T16:36:40', 'items': []}),
        encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr(sync, 'OUTPUT_DIR', out)

    sync.sync_market('kr', src, local_only=True, token=None)

    written = json.loads((out / 'kr.json').read_text(encoding='utf-8'))
    assert written['count'] == 0
    assert written['items'] == []


def test_main_소스_없으면_exit1_푸시_안함(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sync, 'push_to_github', lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')
    rc = sync.main(['--market', 'us', '--source-dir', str(tmp_path)])
    assert rc == 1
    assert calls == []


def test_main_all_한쪽_실패해도_다른쪽은_푸시(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 'leaderboard.json').write_text(
        json.dumps(US_SOURCE, ensure_ascii=False), encoding='utf-8')
    # KR 소스는 일부러 만들지 않는다
    pushed = []
    monkeypatch.setattr(sync, 'push_to_github',
                        lambda market, env, token: pushed.append(market))
    monkeypatch.setattr(sync, 'get_token', lambda: 'fake-token')

    rc = sync.main(['--market', 'all', '--source-dir', str(src)])

    assert rc == 1              # KR 실패
    assert pushed == ['us']     # US는 정상 푸시


def test_get_token_환경변수_우선(monkeypatch):
    monkeypatch.setenv('DASHBOARD_GITHUB_TOKEN', 'env-token')
    assert sync.get_token() == 'env-token'
