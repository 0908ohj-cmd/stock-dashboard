import sys
import types
from datetime import datetime

import numpy as np
import pandas as pd

from data import fetcher


def _df(rows):
    """rows: (날짜, Open, High, Low, Close, Volume)"""
    return pd.DataFrame(
        {
            'Open':   [r[1] for r in rows],
            'High':   [r[2] for r in rows],
            'Low':    [r[3] for r in rows],
            'Close':  [r[4] for r in rows],
            'Volume': [r[5] for r in rows],
        },
        index=pd.to_datetime([r[0] for r in rows]),
    )


class _FakeTicker:
    """yf.Ticker 대역 — fast_info.last_price만 쓴다."""
    def __init__(self, *a, **k):
        pass

    @property
    def fast_info(self):
        return type('F', (), {'last_price': 9999.0})()


def test_today_patch_does_not_create_row(monkeypatch):
    """당일 행이 없어도 새 행을 만들지 않는다.

    last_price는 현재가 한 점이라 O/H/L을 만들 수 없고, O/H/L이 NaN인 행은
    detect_jjin_bounce가 통째로 건너뛰어 그날을 판정 불능으로 만든다.
    """
    df = _df([('2026-09-08', 1.0, 2.0, 0.5, 1.5, 100.0)])
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)

    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')

    assert list(out.index) == list(df.index)        # 행이 늘지 않았다
    assert float(out['Close'].iloc[-1]) == 1.5      # 확정 종가를 덮어쓰지 않는다


def test_today_patch_fills_nan_close(monkeypatch):
    """이미 있는 행의 Close가 NaN이면 현재가로 채운다.

    날짜를 하드코딩하면 실제 달력이 흘러 날짜 가드(2026-09-16 추가)에
    걸려버리므로, 실행 시점의 '오늘'을 그대로 써서 항상 최근 거래일을
    대표하게 한다.
    """
    today = datetime.now(fetcher._KST).strftime('%Y-%m-%d')
    df = _df([(today, 1.0, 2.0, 0.5, np.nan, 100.0)])
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)

    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')

    assert float(out['Close'].iloc[-1]) == 9999.0
    assert len(out) == 1


def test_today_patch_skips_old_row(monkeypatch):
    """마지막 행이 오래된 날짜면 Close가 NaN이어도 현재가를 써넣지 않는다.

    과거 날짜에 오늘 현재가를 종가로 박으면 복구 경로가 모두 실패했을 때
    잘못된 값이 영구 병합된다.
    """
    df = _df([('2020-01-02', 1.0, 2.0, 0.5, np.nan, 100.0)])
    monkeypatch.setattr(fetcher.yf, 'Ticker', _FakeTicker)

    out = fetcher._patch_kr_index_today(df.copy(), '^KS11')

    assert pd.isna(out['Close'].iloc[-1])   # 그대로 NaN


def _fake_pykrx_module(get_index_ohlcv_by_date):
    fake_stock = types.SimpleNamespace(get_index_ohlcv_by_date=get_index_ohlcv_by_date)
    fake_pykrx = types.ModuleType('pykrx')
    fake_pykrx.stock = fake_stock
    return fake_pykrx, fake_stock


def test_fetch_kr_index_pykrx_renames_and_sorts(monkeypatch):
    """pykrx 한글 컬럼을 표준 OHLCV 컬럼으로 바꾸고 날짜 오름차순으로 정렬한다."""
    raw = pd.DataFrame(
        {'시가': [917.45, 902.16], '고가': [918.0, 920.0], '저가': [890.0, 900.0],
         '종가': [898.43, 919.92], '거래량': [602854743.0, 613729940.0]},
        index=pd.to_datetime(['2026-10-07', '2026-10-06']),   # 역순 입력
    )
    fake_pykrx, _ = _fake_pykrx_module(lambda *a, **k: raw)
    monkeypatch.setitem(sys.modules, 'pykrx', fake_pykrx)

    out = fetcher._fetch_kr_index_pykrx('2001', datetime(2026, 10, 1), datetime(2026, 10, 8))

    assert list(out.columns) == ['Open', 'High', 'Low', 'Close', 'Volume']
    assert list(out.index) == sorted(out.index)
    assert float(out.loc['2026-10-07', 'Close']) == 898.43


def test_fetch_kr_index_pykrx_empty_on_failure(monkeypatch):
    """pykrx 호출이 예외를 내면(계정 없음 등) 빈 DataFrame을 반환해 폴백을 유도한다."""
    def _boom(*a, **k):
        raise RuntimeError('KRX 로그인 실패')
    fake_pykrx, _ = _fake_pykrx_module(_boom)
    monkeypatch.setitem(sys.modules, 'pykrx', fake_pykrx)

    out = fetcher._fetch_kr_index_pykrx('2001', datetime(2026, 10, 1), datetime(2026, 10, 8))

    assert out.empty


def test_fetch_index_daily_prefers_pykrx(monkeypatch):
    """pykrx가 데이터를 주면 yfinance를 호출하지 않는다 — 벤더 간 종가 반영
    시점 불일치(코스피는 반영됐는데 코스닥은 하루 지연)가 pykrx 경로엔 없다."""
    fake_df = _df([('2026-10-07', 917.45, 918.0, 890.0, 898.43, 602854743.0)])
    monkeypatch.setattr(fetcher, '_fetch_kr_index_pykrx', lambda *a, **k: fake_df)

    def _boom(*a, **k):
        raise AssertionError('pykrx 성공 시 yfinance를 호출하면 안 된다')
    monkeypatch.setattr(fetcher, '_download', _boom)

    out = fetcher.fetch_index_daily('KOSDAQ')

    assert float(out['Close'].iloc[-1]) == 898.43


def test_fetch_index_daily_falls_back_to_yfinance_when_pykrx_empty(monkeypatch):
    """pykrx가 비면(계정 없음·요청 실패) 기존 yfinance 경로로 넘어간다."""
    monkeypatch.setattr(fetcher, '_fetch_kr_index_pykrx', lambda *a, **k: pd.DataFrame())
    fake_yf_df = _df([('2026-10-07', 1.0, 2.0, 0.5, 1.5, 100.0)])
    monkeypatch.setattr(fetcher, '_download', lambda *a, **k: fake_yf_df.copy())

    out = fetcher.fetch_index_daily('KOSPI')

    assert float(out['Close'].iloc[-1]) == 1.5


def test_incomplete_ohlc_dates_catches_nan_and_flat():
    """NaN 행과 O=H=L=C 평탄 행을 모두 복구 대상으로 잡는다.

    NaN끼리의 비교는 항상 False라 평탄 판정만으로는 NaN 행이 빠진다 —
    2026-09-09 코스피·코스닥 지수가 정확히 이 구멍으로 복구되지 못했다.
    """
    df = _df([
        ('2026-09-07', 1.0, 2.0, 0.5, 1.5, 100.0),                     # 정상
        ('2026-09-08', 3.0, 3.0, 3.0, 3.0, 100.0),                     # 평탄
        ('2026-09-09', np.nan, np.nan, np.nan, 7051.64, 322211.0),     # O/H/L NaN
    ])

    got = [str(ts.date()) for ts in fetcher._incomplete_ohlc_dates(df)]

    assert got == ['2026-09-08', '2026-09-09']
