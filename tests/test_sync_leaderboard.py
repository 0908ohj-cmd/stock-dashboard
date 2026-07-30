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
