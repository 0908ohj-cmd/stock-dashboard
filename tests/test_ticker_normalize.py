"""#39 L7 — 티커 정규화 결손 회귀 테스트.

- KR 종목은 시장 suffix(.KS/.KQ)로 못 받으면 반대 suffix로 1회 재시도한다.
  229200(KOSPI 상장 ETF)이 코스닥 목록에 들어 있어 KR_KOSDAQ.json에 매 배치 실패로 남았다.
- 지수 심볼(KRX:KOSDAQ, NASDAQ:IXIC)은 티커 로드·파싱 단계에서 거른다.
  US.json은 10-27부터 매 배치 failed: ['IXIC'], KR_KOSDAQ.json은 ['KOSDAQ', '229200']였다.
"""
import io
import json

import pandas as pd
import pytest

from data import fetcher, store


def _frame():
    return pd.DataFrame({'Open': [1.0], 'High': [2.0], 'Low': [0.5], 'Close': [1.5], 'Volume': [10.0]},
                        index=pd.to_datetime(['2026-11-12']))


@pytest.fixture
def downloads(monkeypatch):
    """_download 대역 — ok에 든 심볼만 데이터를 준다. 호출한 심볼을 순서대로 기록."""
    calls = []

    def _install(ok):
        def _fake(symbol, start, end, interval='1d'):
            calls.append(symbol)
            return _frame() if symbol in ok else pd.DataFrame()
        monkeypatch.setattr(fetcher, '_download', _fake)
        monkeypatch.setattr(fetcher, '_patch_kr_today', lambda df, t: df)   # 네이버 패치는 이 테스트 밖
        return calls
    return _install


def test_코스닥_목록의_코스피_종목은_KS로_재시도한다(downloads):
    calls = downloads({'229200.KS'})
    df = fetcher.fetch_daily('229200', market='KR_KOSDAQ')
    assert not df.empty
    assert calls == ['229200.KQ', '229200.KS']


def test_코스피_목록의_코스닥_종목은_KQ로_재시도한다(downloads):
    calls = downloads({'035720.KQ'})
    assert not fetcher.fetch_daily('035720', market='KR_KOSPI').empty
    assert calls == ['035720.KS', '035720.KQ']


def test_제_suffix로_받으면_재시도하지_않는다(downloads):
    calls = downloads({'005930.KS'})
    assert not fetcher.fetch_daily('005930', market='KR_KOSPI').empty
    assert calls == ['005930.KS']


def test_txt_파싱은_지수_심볼을_거른다():
    content = 'KRX:229200,KRX:KOSDAQ,###그룹,KRX:200470\nNASDAQ:IXIC,NASDAQ:QQQ,TVC:SPX,AAPL'
    assert fetcher.parse_ticker_txt(content) == ['229200', '200470', 'QQQ', 'AAPL']


def test_csv_파싱은_지수_심볼을_거른다():
    csv = 'Symbol,Price\nNASDAQ:IXIC,1\nNASDAQ:NVDA,2\nKRX:KOSPI,3\n'
    df = fetcher.parse_tradingview_csv(io.BytesIO(csv.encode()))
    assert df['Ticker'].tolist() == ['NVDA']


@pytest.fixture
def saved_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'OHLCV_DIR', tmp_path)
    monkeypatch.setattr(store, '_snap_cache', {})
    d = tmp_path / 'saved'
    d.mkdir()
    monkeypatch.setattr(store, 'SAVED_DIR', d)
    return d


def test_스냅샷_종목_목록은_지수_심볼과_6자리_코드가_아닌_KR_티커를_거른다(saved_dir):
    """data/saved/*.tickers에 이미 들어간 지수 심볼(파일은 그대로 둔다)도 배치 유니버스에서 빠진다."""
    (saved_dir / 'us.tickers').write_text('QQQ\nIXIC\nKRMN\n', encoding='utf-8')
    (saved_dir / 'kosdaq.tickers').write_text('KOSDAQ\n229200\n0091C0\n033790\n', encoding='utf-8')
    assert store.snapshot_tickers('US') == ['QQQ', 'KRMN']
    assert store.snapshot_tickers('KR_KOSDAQ') == ['229200', '0091C0', '033790']


def test_load_daily는_지수_심볼을_수집하지_않는다(saved_dir, monkeypatch):
    """앱은 저장된 티커 파일을 그대로 읽는다 — 지수 심볼은 스냅샷 미스여도 온디맨드 수집을 하지 않는다."""
    monkeypatch.setattr(store, 'fetch_daily', lambda *a, **k: pytest.fail('지수 심볼 수집 금지'))
    assert store.load_daily('IXIC', 'US').empty
    assert store.load_daily('KOSDAQ', 'KR_KOSDAQ').empty
    assert not (saved_dir.parent / 'US.json').exists()
