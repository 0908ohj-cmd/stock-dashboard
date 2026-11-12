"""KR 시세 패치의 pykrx 호출 수 회귀 테스트 (#34).

2026-08 KRX가 "자동화 수단을 통한 비정상 대량 조회"로 IP를 1일 차단했다. 패치가
종목마다 pykrx를 부르면 휴장 다음 날 배치 한 번에 261회(재수집까지 최대 ~780회)가
나간다. 여기서는 진짜 pykrx를 절대 import하지 않는다 — 가짜 모듈을 sys.modules에
꽂고 **모든 함수의 호출 수**를 센다(어떤 API를 쓰든 총 호출 수가 계약이다).
"""
import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from data import fetcher, store


# ── 가짜 pykrx ─────────────────────────────────────────────
_STOCK_COLS = ['시가', '고가', '저가', '종가', '거래량', '거래대금', '등락률', '시가총액']
_ETF_COLS = ['NAV', '시가', '고가', '저가', '종가', '거래량', '거래대금', '기초지수']
_INDEX_COLS = ['시가', '고가', '저가', '종가', '거래량', '거래대금', '상장시가총액']


class FakePykrx:
    """pykrx 1.2.8의 stock 모듈 대역. 반환 구조는 실제 소스(site-packages/pykrx)와 같게 맞춘다.

    stocks: {'YYYYMMDD': {ticker: (시가, 고가, 저가, 종가, 거래량)}} — 없는 날짜는 휴장
    etfs:   같은 형식의 ETF 시세
    markets: {ticker: 'KOSPI'|'KOSDAQ'} — 전종목 조회의 market 필터용
    """

    def __init__(self, stocks=None, etfs=None, markets=None, raise_exc=None, delay=0.0):
        self.stocks = stocks or {}
        self.etfs = etfs or {}
        self.markets = markets or {}
        self.raise_exc = raise_exc
        self.delay = delay
        self.calls = []
        self._lock = threading.Lock()

    # 호출 기록 공통부 ------------------------------------------------
    def _hit(self, name, *args, **kwargs):
        with self._lock:
            self.calls.append((name, args, kwargs))
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc

    def _all_tickers(self):
        out = set(self.markets)
        for day in self.stocks.values():
            out.update(day)
        return sorted(out)

    # 종목별 기간 조회 (adjusted=True면 실제로는 네이버) ------------------
    def get_market_ohlcv_by_date(self, fromdate, todate, ticker, freq='d',
                                 adjusted=True, name_display=False):
        self._hit('get_market_ohlcv_by_date', fromdate, todate, ticker)
        rows, idx = [], []
        for d in sorted(set(self.stocks) | set(self.etfs)):
            if not (fromdate <= d <= todate):
                continue
            v = self.stocks.get(d, {}).get(ticker) or self.etfs.get(d, {}).get(ticker)
            if v:
                rows.append(list(v) + [0.0])
                idx.append(pd.Timestamp(d))
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows, columns=['시가', '고가', '저가', '종가', '거래량', '등락률'],
                            index=pd.DatetimeIndex(idx, name='날짜'))

    # 특정 일자 전종목 조회 ---------------------------------------------
    def get_market_ohlcv_by_ticker(self, date, market='KOSPI', alternative=False):
        self._hit('get_market_ohlcv_by_ticker', date, market=market)
        return self._stock_section(date, market)

    def get_market_ohlcv(self, *args, **kwargs):
        dates = [a for a in args if isinstance(a, str) and a.isdigit() and len(a) == 8]
        if len(dates) == 2:
            return self.get_market_ohlcv_by_date(*args, **kwargs)
        self._hit('get_market_ohlcv', *args, **kwargs)
        market = kwargs.get('market', args[1] if len(args) > 1 else 'KOSPI')
        return self._stock_section(args[0], market)

    def _stock_section(self, date, market):
        if market not in ('ALL', 'KOSPI', 'KOSDAQ', 'KONEX'):
            return pd.DataFrame()
        tickers = [t for t in self._all_tickers()
                   if market == 'ALL' or self.markets.get(t, 'KOSPI') == market]
        day = self.stocks.get(date)
        rows = []
        for t in tickers:
            if day is None:                          # 휴장일 — KRX는 전 종목을 0으로 준다
                rows.append([0, 0, 0, 0, 0, 0, 0.0, 0])
            elif t in day:
                o, h, lo, c, v = day[t]
                rows.append([o, h, lo, c, v, int(c * v), 0.0, int(c * 1_000_000)])
        df = pd.DataFrame(rows, columns=_STOCK_COLS, index=pd.Index(
            [t for t in tickers if day is None or t in day], name='티커'))
        return df

    def get_etf_ohlcv_by_ticker(self, date):
        self._hit('get_etf_ohlcv_by_ticker', date)
        day = self.etfs.get(date)
        if not day:
            return pd.DataFrame()                    # 휴일이면 빈 DataFrame (pykrx 문서)
        rows = [[float(c), o, h, lo, c, v, int(c * v), 100.0] for (o, h, lo, c, v) in day.values()]
        return pd.DataFrame(rows, columns=_ETF_COLS, index=pd.Index(list(day), name='티커'))

    # 지수 ------------------------------------------------------------
    def get_index_ohlcv_by_date(self, fromdate, todate, ticker, freq='d', name_display=False):
        self._hit('get_index_ohlcv_by_date', fromdate, todate, ticker)
        return pd.DataFrame(columns=_INDEX_COLS)    # 테스트에서는 항상 빈 응답

    def module(self):
        mod = types.ModuleType('pykrx')
        mod.stock = self
        return mod


@pytest.fixture
def fake_pykrx(monkeypatch):
    def _install(**kwargs):
        fake = FakePykrx(**kwargs)
        monkeypatch.setitem(sys.modules, 'pykrx', fake.module())
        return fake
    return _install


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """실제 data/ohlcv/*.json을 읽거나 덮어쓰지 않게 저장소 경로를 임시 디렉터리로 돌리고,
    프로세스 내 캐시를 테스트마다 비운다. 혹시라도 진짜 FDR이 불리지 않게 막는다."""
    monkeypatch.setattr(store, 'OHLCV_DIR', tmp_path)
    monkeypatch.setattr(store, '_snap_cache', {})
    monkeypatch.setattr(fetcher, '_KR_XS_CACHE', {}, raising=False)

    def _no_fdr(*a, **k):
        raise RuntimeError('FDR 호출 금지 (테스트)')
    monkeypatch.setitem(sys.modules, 'FinanceDataReader',
                        types.SimpleNamespace(DataReader=_no_fdr, StockListing=_no_fdr))
    return tmp_path


def _freeze(monkeypatch, kst_iso):
    """fetcher가 보는 '지금'을 KST 기준으로 고정한다 (today()·now() 모두)."""
    fixed = datetime.fromisoformat(kst_iso).replace(tzinfo=fetcher._KST)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        @classmethod
        def today(cls):
            return fixed.replace(tzinfo=None)

    monkeypatch.setattr(fetcher, 'datetime', _Frozen)


def _write_indices(tmp_path, dates):
    """배치가 먼저 받아 둔 indices 스냅샷(KOSPI·KOSDAQ) — 휴장일 판정의 근거."""
    n = len(dates)
    rec = {'dates': dates, 'open': [1.0] * n, 'high': [2.0] * n, 'low': [0.5] * n,
           'close': [1.5] * n, 'volume': [100.0] * n}
    snap = {'market': 'indices', 'data': {'KOSPI': rec, 'KOSDAQ': rec}}
    (tmp_path / 'indices.json').write_text(json.dumps(snap), encoding='utf-8')


def _yf_frame(dates, last_nan=False, base=100.0):
    """yfinance 대역 프레임. last_nan이면 마지막 행 전체가 NaN (장 마감 후 흔한 형태)."""
    n = len(dates)
    df = pd.DataFrame({
        'Open':   [base] * n, 'High': [base + 5] * n, 'Low': [base - 5] * n,
        'Close':  [base + 1] * n, 'Volume': [1000.0] * n,
    }, index=pd.DatetimeIndex(pd.to_datetime(dates)))
    if last_nan:
        df.iloc[-1] = np.nan
    return df


def _install_yf(monkeypatch, dates, last_nan=False):
    monkeypatch.setattr(fetcher, '_download',
                        lambda t, s, e, interval='1d': _yf_frame(dates, last_nan))


TICKERS = [f'{100000 + i:06d}' for i in range(261)]


def _session(tickers, offset=0):
    """종목마다 다른 값 — 다른 종목의 행을 잘못 꺼내 쓰면 드러난다."""
    return {t: (1000 + i + offset, 1100 + i + offset, 900 + i + offset,
                1050 + i + offset, 10_000 + i) for i, t in enumerate(tickers)}


# ── (a) 휴장 다음 날 ───────────────────────────────────────
def test_holiday_batch_261_tickers_calls_pykrx_at_most_once(monkeypatch, fake_pykrx, _isolate):
    """2027-01-01 17:30 KR 배치 — 직전 평일 12/31이 휴장. 지연 재수집까지 두 번 돌려도
    pykrx는 최대 1회만 나가야 한다 (수정 전: 패스마다 261회)."""
    _freeze(monkeypatch, '2027-01-01 17:30')
    _write_indices(_isolate, ['2026-12-29', '2026-12-30'])
    _install_yf(monkeypatch, ['2026-12-29', '2026-12-30'])
    fake = fake_pykrx(stocks={'20261229': _session(TICKERS), '20261230': _session(TICKERS)})

    snaps = [store.build_market_snapshot('KR_KOSPI', TICKERS, throttle_sec=0) for _ in range(2)]

    assert len(fake.calls) <= 1
    for snap in snaps:
        assert snap['ticker_count'] == 261
        assert snap['last_trading_date'] == '2026-12-30'   # 휴장일 행을 만들지 않는다


def test_known_holiday_from_index_snapshot_skips_pykrx(monkeypatch, fake_pykrx, _isolate):
    """2027-01-04(월) 배치 — 직전 평일 1/1은 지수 스냅샷에 그 뒤 거래일(1/4)이 있는데
    1/1만 없으니 휴장으로 확정된다. 이때는 조회 자체를 하지 않는다."""
    _freeze(monkeypatch, '2027-01-04 17:30')
    _write_indices(_isolate, ['2026-12-29', '2026-12-30', '2027-01-04'])
    _install_yf(monkeypatch, ['2026-12-29', '2026-12-30'])   # 지연된 종목들
    fake = fake_pykrx(stocks={'20261230': _session(TICKERS), '20270104': _session(TICKERS)})

    store.build_market_snapshot('KR_KOSPI', TICKERS, throttle_sec=0)

    assert fake.calls == []


def test_today_with_no_later_index_date_is_not_treated_as_holiday(monkeypatch, fake_pykrx,
                                                                   _isolate):
    """오늘 날짜의 '데이터 없음'을 휴장으로 굳히지 않는다 — 지수 스냅샷에 오늘이 아직
    없어도 오늘 NaN 행은 조회해서 채운다."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-11', '2026-11-12'])   # 지수에 오늘(11/13)이 아직 없다
    _install_yf(monkeypatch, ['2026-11-12', '2026-11-13'], last_nan=True)
    fake = fake_pykrx(stocks={'20261113': _session(['005930'])})

    out = fetcher.fetch_daily('005930', market='KR_KOSPI')

    assert out.index[-1] == pd.Timestamp('2026-11-13')
    assert float(out['Close'].iloc[-1]) == 1050.0
    assert len(fake.calls) == 1


# ── (b) 마지막 행 NaN인 평일 ───────────────────────────────
def test_nan_last_row_261_tickers_one_cross_section_call(monkeypatch, fake_pykrx, _isolate):
    """평일 장 마감 후 yfinance 마지막 행이 NaN — 코스피·코스닥 261종목을 날짜당 전종목
    조회 1회로 채우고, 각 종목에는 자기 행의 값이 들어가야 한다."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-11', '2026-11-12', '2026-11-13'])
    _install_yf(monkeypatch, ['2026-11-11', '2026-11-12', '2026-11-13'], last_nan=True)
    kospi, kosdaq = TICKERS[:130], TICKERS[130:]
    markets = {**{t: 'KOSPI' for t in kospi}, **{t: 'KOSDAQ' for t in kosdaq}}
    fake = fake_pykrx(stocks={'20261113': _session(TICKERS)}, markets=markets)

    snap_p = store.build_market_snapshot('KR_KOSPI', kospi, throttle_sec=0)
    snap_q = store.build_market_snapshot('KR_KOSDAQ', kosdaq, throttle_sec=0)

    assert len(fake.calls) == 1
    data = {**snap_p['data'], **snap_q['data']}
    assert snap_p['last_trading_date'] == snap_q['last_trading_date'] == '2026-11-13'
    # 손으로 계산한 기대값: i번째 종목 = (1000+i, 1100+i, 900+i, 1050+i, 10000+i)
    for i in (0, 1, 129, 130, 260):
        rec = data[TICKERS[i]]
        assert rec['dates'][-1] == '2026-11-13'
        assert (rec['open'][-1], rec['high'][-1], rec['low'][-1],
                rec['close'][-1], rec['volume'][-1]) == (
            1000 + i, 1100 + i, 900 + i, 1050 + i, 10_000 + i)


def test_missing_previous_weekday_row_filled_from_cross_section(monkeypatch, fake_pykrx,
                                                                _isolate):
    """직전 평일 행이 아예 없으면 그 날짜의 전종목 시세로 행을 추가한다 (기존 동작 유지)."""
    _freeze(monkeypatch, '2026-11-13 08:00')                 # 장 시작 전 — 오늘 행은 아직 없음
    _write_indices(_isolate, ['2026-11-10', '2026-11-11'])
    _install_yf(monkeypatch, ['2026-11-10', '2026-11-11'])   # 11/12 행 누락
    fake = fake_pykrx(stocks={'20261112': _session(['000660', '005930'])})

    out = fetcher.fetch_daily('005930', market='KR_KOSPI')

    assert out.index[-1] == pd.Timestamp('2026-11-12')
    assert list(out.iloc[-1]) == [1001.0, 1101.0, 901.0, 1051.0, 10_001.0]   # 005930 = i=1
    assert len(fake.calls) == 1


def test_etf_missing_from_stock_section_uses_etf_section_once(monkeypatch, fake_pykrx,
                                                              _isolate):
    """KODEX 200(069500) 같은 ETF는 주식 전종목 시세에 없다 — ETF 전종목 시세를 날짜당
    1회만 더 조회해 채운다 (기존 종목별 경로는 ETF도 채웠다)."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-12', '2026-11-13'])
    _install_yf(monkeypatch, ['2026-11-12', '2026-11-13'], last_nan=True)
    fake = fake_pykrx(stocks={'20261113': _session(['005930'])},
                      etfs={'20261113': {'069500': (40000, 40500, 39800, 40300, 5_000_000),
                                         '229200': (12000, 12100, 11900, 12050, 3_000_000)}})

    snap = store.build_market_snapshot('KR_KOSPI', ['005930', '069500', '229200'],
                                       throttle_sec=0)

    rec = snap['data']['069500']
    assert (rec['open'][-1], rec['high'][-1], rec['low'][-1], rec['close'][-1],
            rec['volume'][-1]) == (40000, 40500, 39800, 40300, 5_000_000)
    assert snap['data']['229200']['close'][-1] == 12050
    assert snap['data']['005930']['close'][-1] == 1050
    assert len(fake.calls) == 2                               # 주식 전종목 1 + ETF 전종목 1


def test_suspended_zero_ohlc_is_not_written(monkeypatch, fake_pykrx, _isolate):
    """거래정지 종목은 전종목 시세에서 시가·고가·저가가 0이다 — 0을 써넣으면 저가 0원
    봉이 생겨 ADR·EMA를 오염시키므로 그 날은 채우지 않는다."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-12', '2026-11-13'])
    _install_yf(monkeypatch, ['2026-11-12', '2026-11-13'], last_nan=True)
    fake_pykrx(stocks={'20261113': {'005930': (0, 0, 0, 51000, 0)}})

    out = fetcher.fetch_daily('005930', market='KR_KOSPI')

    assert out.index[-1] == pd.Timestamp('2026-11-12')       # NaN 행은 dropna로 빠진다
    assert (out[['Open', 'High', 'Low']] > 0).all().all()


# ── (c) 정상 ──────────────────────────────────────────────
def test_complete_data_makes_no_pykrx_call(monkeypatch, fake_pykrx, _isolate):
    """오늘 행까지 정상인 평일 — 패치 대상이 없으면 pykrx를 부르지 않는다."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-12', '2026-11-13'])
    _install_yf(monkeypatch, ['2026-11-12', '2026-11-13'])
    fake = fake_pykrx(stocks={'20261113': _session(TICKERS)})

    snap = store.build_market_snapshot('KR_KOSPI', TICKERS, throttle_sec=0)

    assert fake.calls == []
    assert snap['data'][TICKERS[0]]['close'][-1] == 101.0


# ── 캐시 수명·예외·동시성 ──────────────────────────────────
def test_cross_section_failure_is_not_retried_per_ticker(monkeypatch, fake_pykrx, _isolate):
    """KRX 로그인 실패·차단처럼 조회가 예외를 내도 종목마다 다시 두드리지 않는다."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-12', '2026-11-13'])
    _install_yf(monkeypatch, ['2026-11-12', '2026-11-13'], last_nan=True)
    fake = fake_pykrx(raise_exc=KeyError('시가'))

    snap = store.build_market_snapshot('KR_KOSPI', TICKERS, throttle_sec=0)

    assert len(fake.calls) == 1
    assert snap['last_trading_date'] == '2026-11-12'


def test_unsettled_response_expires_but_settled_one_is_kept(monkeypatch, fake_pykrx, _isolate):
    """오늘 날짜 응답(장중·미확정)은 일정 시간 뒤 다시 조회할 수 있어야 하고 — 오래 사는
    Streamlit 프로세스에서 오전의 빈 응답이 종일 굳으면 안 된다 — 지난 날짜 응답은
    바뀌지 않으므로 계속 재사용한다."""
    _freeze(monkeypatch, '2026-11-13 10:00')
    _write_indices(_isolate, ['2026-11-11', '2026-11-12'])
    clock = [1000.0]
    monkeypatch.setattr(fetcher, '_clock', lambda: clock[0], raising=False)
    fake = fake_pykrx(stocks={'20261112': _session(['005930', '000660'])})
    today_nan = _yf_frame(['2026-11-12', '2026-11-13'], last_nan=True)
    stale = _yf_frame(['2026-11-10', '2026-11-11'])           # 직전 평일(11/12) 행 누락

    fetcher._patch_kr_today(today_nan.copy(), '005930')       # 오늘(11/13) 조회 → 빈 응답
    fetcher._patch_kr_today(stale.copy(), '005930')           # 11/12 조회 → 확정 데이터
    assert len(fake.calls) == 2

    fetcher._patch_kr_today(today_nan.copy(), '000660')       # 같은 시각: 둘 다 캐시
    fetcher._patch_kr_today(stale.copy(), '000660')
    assert len(fake.calls) == 2

    clock[0] += 6 * 3600                                       # 몇 시간 뒤
    fetcher._patch_kr_today(today_nan.copy(), '000660')       # 오늘 날짜는 다시 조회
    fetcher._patch_kr_today(stale.copy(), '000660')           # 지난 날짜는 그대로 캐시
    assert [c[1][0] for c in fake.calls] == ['20261113', '20261112', '20261113']


def test_parallel_misses_share_one_call(monkeypatch, fake_pykrx, _isolate):
    """10EMA 탭은 스레드 10개로 미스 폴백을 돌린다 — 동시에 들어와도 조회는 1회."""
    _freeze(monkeypatch, '2026-11-13 17:30')
    _write_indices(_isolate, ['2026-11-12', '2026-11-13'])
    _install_yf(monkeypatch, ['2026-11-12', '2026-11-13'], last_nan=True)
    fake = fake_pykrx(stocks={'20261113': _session(TICKERS[:20])}, delay=0.05)

    with ThreadPoolExecutor(max_workers=10) as ex:
        outs = list(ex.map(lambda t: fetcher.fetch_daily(t, market='KR_KOSPI'), TICKERS[:20]))

    assert len(fake.calls) == 1
    assert all(o.index[-1] == pd.Timestamp('2026-11-13') for o in outs)
