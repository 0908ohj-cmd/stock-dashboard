"""#39 M1 — 시장 last_trading_date(전 종목 최댓값)가 일부 종목의 지연·실패를 묻는 문제.

- 판정은 '같은 시장 지수의 마지막 날짜에 도달한 종목 비율'로 한다 (MIN_REACHED_RATIO).
  2026-10-24 US 배치는 252종목 중 53종목(21%)이 하루 늦었는데 최댓값 판정이라 경고가 없었다.
  2026-10~11 실측 평소 지연은 0~2종목(≤1.2%) — 오경보 없이 잡히는 문턱이다.
- 지연 종목은 앱 신선도 캡션에 '지연 n종목'으로 보인다.
- 수집 실패 종목의 옛 이력은 FAILED_KEEP_TRADING_DAYS 거래일까지만 보존한다.
"""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from data import store
from scripts import check_snapshot_fresh as cs
from scripts import fetch_snapshot as fs

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=KST)          # 목 18:00 — KR 17:30 배치 직후
FETCHED = '2026-11-12T17:34:00+09:00'


def _rec(last: str) -> dict:
    dates = ['2026-11-10', '2026-11-11', '2026-11-12']
    dates = [d for d in dates if d <= last]
    n = len(dates)
    return {'dates': dates, 'open': [1.0] * n, 'high': [2.0] * n, 'low': [0.5] * n,
            'close': [1.5] * n, 'volume': [100.0] * n}


def _snap(market, lasts: dict, failed=()) -> dict:
    return {'market': market, 'fetched_at': FETCHED,
            'last_trading_date': max(lasts.values()),
            'ticker_count': len(lasts) - len(failed), 'failed': list(failed),
            'data': {t: _rec(d) for t, d in lasts.items()}}


def _lasts(n_current: int, n_lagging: int) -> dict:
    out = {f'{i:06d}': '2026-11-12' for i in range(n_current)}
    out.update({f'{900000 + i:06d}': '2026-11-11' for i in range(n_lagging)})
    return out


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'OHLCV_DIR', tmp_path)
    monkeypatch.setattr(store, '_snap_cache', {})
    # 배치 유니버스(snapshot_tickers)는 기본 비어 있음 — 비면 스냅샷 전 종목으로 판정한다
    monkeypatch.setattr(store, 'SAVED_DIR', tmp_path / 'saved')
    return tmp_path


def _write(tmp, name, snap):
    (tmp / name).write_text(json.dumps(snap), encoding='utf-8')


def _write_indices(tmp, kosdaq='2026-11-12'):
    _write(tmp, 'indices.json', {'market': 'indices', 'fetched_at': FETCHED, 'data': {
        'KOSPI': _rec('2026-11-12'), 'KOSDAQ': _rec(kosdaq)}})


# ── 앱 신선도 (store.get_freshness) ─────────────────────────

def test_한_종목만_최신이어도_다수가_지수보다_늦으면_stale(tmp_store):
    _write(tmp_store, 'KR_KOSDAQ.json', _snap('KR_KOSDAQ', _lasts(7, 3)))
    _write_indices(tmp_store)

    fr = store.get_freshness('KR_KOSDAQ', now=NOW)

    assert fr['last_trading_date'] == '2026-11-12'          # 최댓값은 지수와 같다
    assert fr['is_stale'] is True
    assert fr['behind_index'] == '2026-11-12'
    assert fr['lagging'] == ['900000', '900001', '900002']


def test_소수_종목_지연은_신선하되_지연_종목을_노출한다(tmp_store):
    """실데이터: KOSDAQ 032580 하나만 11-11, 나머지 159종목은 11-12."""
    lasts = _lasts(159, 0)
    lasts['032580'] = '2026-11-11'
    _write(tmp_store, 'KR_KOSDAQ.json', _snap('KR_KOSDAQ', lasts, failed=['032580']))
    _write_indices(tmp_store)

    fr = store.get_freshness('KR_KOSDAQ', now=NOW)

    assert fr['is_stale'] is False and 'behind_index' not in fr
    assert fr['lagging'] == ['032580']
    assert fr['failed'] == ['032580']


def test_지수가_늦어도_지연_종목은_시장_최신일_기준으로_센다(tmp_store):
    """#24 반대 방향과 함께 — 지수 11-11, 종목 최신 11-12면 11-12에 못 미친 종목이 지연이다."""
    _write(tmp_store, 'KR_KOSDAQ.json', _snap('KR_KOSDAQ', _lasts(9, 1)))
    _write_indices(tmp_store, kosdaq='2026-11-11')

    fr = store.get_freshness('KR_KOSDAQ', now=NOW)

    assert fr['index_behind'] == '2026-11-11' and fr['is_stale'] is True
    assert fr['lagging'] == ['900000']


def test_배치_유니버스_밖_잔여_종목은_지연_판정에서_뺀다(tmp_store):
    """업로드로 목록에서 빠진 옛 종목은 다음 배치까지 스냅샷에 남는다 — 🔄 재수집으로도 갱신되지 않으므로
    그 종목 때문에 경고가 풀리지 않으면 안 된다."""
    lasts = _lasts(7, 3)
    (tmp_store / 'saved').mkdir()
    (tmp_store / 'saved' / 'kosdaq.tickers').write_text(
        '\n'.join(t for t, d in lasts.items() if d == '2026-11-12'), encoding='utf-8')
    _write(tmp_store, 'KR_KOSDAQ.json', _snap('KR_KOSDAQ', lasts))
    _write_indices(tmp_store)

    fr = store.get_freshness('KR_KOSDAQ', now=NOW)

    assert fr['is_stale'] is False and 'behind_index' not in fr
    assert fr['lagging'] == []


def test_lag_note_캡션_꼬리():
    assert store.lag_note({'lagging': [], 'failed': []}) == ''
    assert store.lag_note({}) == ''
    assert store.lag_note({'lagging': ['032580'], 'failed': ['032580', 'ZZZZ']}) == \
        ' · ⚠️ 지연 1종목(032580) · 수집 실패 1종목(ZZZZ)'
    assert store.lag_note({'lagging': list('ABCDE'), 'failed': []}) == ' · ⚠️ 지연 5종목(A, B, C 외 2)'


# ── 실패 종목 보존 한도 ──────────────────────────────────────

def _frame(dates):
    n = len(dates)
    return pd.DataFrame({'Open': [1.0] * n, 'High': [2.0] * n, 'Low': [0.5] * n,
                         'Close': [1.5] * n, 'Volume': [100.0] * n},
                        index=pd.DatetimeIndex(pd.to_datetime(dates)))


def test_실패_종목의_옛_이력은_N거래일까지만_보존한다(tmp_store, capsys):
    days = list(pd.bdate_range('2026-11-02', '2026-11-12').strftime('%Y-%m-%d'))   # 9거래일
    keep_last = days[-1 - store.FAILED_KEEP_TRADING_DAYS]      # N거래일 뒤처짐 → 보존
    drop_last = days[-2 - store.FAILED_KEEP_TRADING_DAYS]      # N+1거래일 뒤처짐 → 버림
    old = {'market': 'KR_KOSPI', 'data': {
        'KEEP01': store._df_to_records(_frame([d for d in days if d <= keep_last])),
        'DROP01': store._df_to_records(_frame([d for d in days if d <= drop_last])),
    }}
    _write(tmp_store, 'KR_KOSPI.json', old)

    def fetch(t, market, days=None):
        return _frame(days_all) if t == '005930' else pd.DataFrame()
    days_all = days

    snap = store.build_market_snapshot('KR_KOSPI', ['005930', 'KEEP01', 'DROP01'],
                                       fetch_fn=fetch, throttle_sec=0)

    assert sorted(snap['failed']) == ['DROP01', 'KEEP01']
    assert 'KEEP01' in snap['data'] and 'DROP01' not in snap['data']
    assert 'DROP01' in capsys.readouterr().out


# ── 폴백 게이트 (check_snapshot_fresh, 표준 라이브러리만) ─────

def test_게이트_문턱은_앱과_같다():
    assert cs.MIN_REACHED_RATIO == store.MIN_REACHED_RATIO


def test_게이트_한_종목만_최신이어도_다수가_늦으면_미달(tmp_path):
    for n in ('KR_KOSPI.json', 'KR_KOSDAQ.json'):
        _write(tmp_path, n, _snap(n[:-5], _lasts(7, 3)))
    _write_indices(tmp_path)
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is False

    for n in ('KR_KOSPI.json', 'KR_KOSDAQ.json'):
        _write(tmp_path, n, _snap(n[:-5], _lasts(159, 1)))
    assert cs.is_fresh('kr', now=NOW, ohlcv_dir=tmp_path) is True


# ── 배치 지연 재수집 (fetch_snapshot.run_market) ─────────────

def test_배치는_다수_종목이_늦으면_최댓값이_같아도_종목을_재수집한다(monkeypatch):
    monkeypatch.setattr(fs, 'load_tickers', lambda m: ['A'])
    monkeypatch.setattr(fs, '_index_last', lambda m: '2026-11-12')
    snaps = iter([_snap('US', _lasts(7, 3)), _snap('US', _lasts(10, 0))])
    monkeypatch.setattr(fs.store, 'build_market_snapshot', lambda *a, **k: next(snaps))
    saved, slept = [], []
    monkeypatch.setattr(fs.store, 'save_snapshot', lambda m, s: saved.append(s))

    assert fs.run_market('US', waits=(1, 2), sleep=slept.append) is True
    assert slept == [1]
    assert store.lagging_tickers(saved[0], '2026-11-12') == []
