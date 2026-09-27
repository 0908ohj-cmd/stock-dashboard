import json

from scripts import merge_snapshots as ms


def _rec(dates, close):
    """스냅샷의 종목 레코드 한 개 — store._df_to_records와 같은 모양."""
    n = len(dates)
    return {
        'dates': dates,
        'open': [close] * n, 'high': [close + 1] * n, 'low': [close - 1] * n,
        'close': [close] * n, 'volume': [1000.0] * n,
    }


def _snap(market, fetched_at, ltd, data, failed=()):
    return {
        'market': market, 'fetched_at': fetched_at, 'last_trading_date': ltd,
        'ticker_count': len(data) - len(set(failed) & set(data)),
        'failed': list(failed), 'data': data,
    }


def test_union_of_tickers():
    """한쪽에만 있는 종목도 모두 남는다."""
    a = _snap('KR_KOSPI', '2026-09-16T16:44:00+09:00', '2026-09-16',
              {'005930': _rec(['2026-09-15', '2026-09-16'], 100.0)})
    b = _snap('KR_KOSPI', '2026-09-16T20:05:00+09:00', '2026-09-16',
              {'000660': _rec(['2026-09-15', '2026-09-16'], 200.0)})

    m = ms.merge_snapshot(a, b, 'KR_KOSPI')

    assert set(m['data']) == {'005930', '000660'}
    assert m['ticker_count'] == 2


def test_newer_fetch_wins_on_overlapping_dates():
    """같은 날짜가 겹치면 나중에 수집한 쪽의 값을 쓴다 — 수정주가가 더 확정된 쪽."""
    older = _snap('US', '2026-09-16T09:03:00+09:00', '2026-09-15',
                  {'AAPL': _rec(['2026-09-14', '2026-09-15'], 100.0)})
    newer = _snap('US', '2026-09-16T13:04:00+09:00', '2026-09-15',
                  {'AAPL': _rec(['2026-09-14', '2026-09-15'], 105.0)})

    m = ms.merge_snapshot(older, newer, 'US')   # 인자 순서와 무관해야 한다

    assert m['data']['AAPL']['close'] == [105.0, 105.0]
    assert m['fetched_at'] == '2026-09-16T13:04:00+09:00'


def test_older_only_dates_are_preserved():
    """한쪽 수집분에 빠진 거래일은 다른 쪽에서 보존한다.

    같은 날을 두 번 수집하면 종가는 같다. 겹치는 날짜의 종가가 다르면 store가
    수정주가 재조정(분할·배당)으로 보고 옛 행을 버리는데, 그건 의도된 동작이다.
    """
    a = _snap('KR_KOSPI', '2026-09-16T16:44:00+09:00', '2026-09-16',
              {'005930': _rec(['2026-09-14', '2026-09-15', '2026-09-16'], 100.0)})
    b = _snap('KR_KOSPI', '2026-09-16T20:05:00+09:00', '2026-09-16',
              {'005930': _rec(['2026-09-15', '2026-09-16'], 100.0)})

    m = ms.merge_snapshot(a, b, 'KR_KOSPI')

    assert m['data']['005930']['dates'] == ['2026-09-14', '2026-09-15', '2026-09-16']


def test_last_trading_date_is_the_later_one():
    a = _snap('indices', '2026-09-16T16:44:00+09:00', '2026-09-16',
              {'KOSPI': _rec(['2026-09-16'], 7000.0)})
    b = _snap('indices', '2026-09-16T13:04:00+09:00', '2026-09-15',
              {'NASDAQ': _rec(['2026-09-15'], 26000.0)})

    m = ms.merge_snapshot(a, b, 'indices')

    assert m['last_trading_date'] == '2026-09-16'


def test_cross_market_indices_keep_both_updates():
    """KR 배치와 US 배치가 indices.json을 동시에 고쳐도 양쪽 갱신이 모두 남는다.

    indices.json 한 파일에 KOSPI·KOSDAQ·NASDAQ이 함께 들어 있어, 통째로 한쪽을
    고르면 다른 시장의 지수 갱신이 사라진다.
    """
    kr = _snap('indices', '2026-09-16T16:44:00+09:00', '2026-09-16', {
        'KOSPI':  _rec(['2026-09-15', '2026-09-16'], 7000.0),
        'NASDAQ': _rec(['2026-09-14'], 26000.0),
    })
    us = _snap('indices', '2026-09-16T13:04:00+09:00', '2026-09-15', {
        'KOSPI':  _rec(['2026-09-15'], 7000.0),
        'NASDAQ': _rec(['2026-09-14', '2026-09-15'], 26100.0),
    })

    m = ms.merge_snapshot(kr, us, 'indices')

    assert m['data']['KOSPI']['dates'][-1] == '2026-09-16'    # KR 쪽 갱신 유지
    assert m['data']['NASDAQ']['dates'][-1] == '2026-09-15'   # US 쪽 갱신 유지


def test_cli_merges_files_in_place(tmp_path):
    """CLI는 ours·theirs를 읽어 out에 쓴다 — out이 theirs와 같은 경로여도 된다."""
    ours = tmp_path / 'ours' / 'KR_KOSPI.json'
    theirs = tmp_path / 'KR_KOSPI.json'
    ours.parent.mkdir()
    ours.write_text(json.dumps(_snap('KR_KOSPI', '2026-09-16T16:44:00+09:00', '2026-09-16',
                                     {'005930': _rec(['2026-09-16'], 100.0)})), encoding='utf-8')
    theirs.write_text(json.dumps(_snap('KR_KOSPI', '2026-09-16T20:05:00+09:00', '2026-09-16',
                                       {'000660': _rec(['2026-09-16'], 200.0)})), encoding='utf-8')

    assert ms.main([str(ours), str(theirs), str(theirs)]) == 0

    out = json.loads(theirs.read_text(encoding='utf-8'))
    assert set(out['data']) == {'005930', '000660'}
    assert out['market'] == 'KR_KOSPI'
