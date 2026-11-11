"""지수 현재가 실시간 조회 — 지수 현황 패널의 현재가·등락률 전용.

Streamlit 무의존. 국면·ADR·PP 판정은 여기 값을 쓰지 않는다(배치가 확정한 일봉 기준).

소스는 yfinance 하나다. pykrx(KRX 로그인)는 부르지 않는다 — Cloud에서 접속마다 KRX를
치면 IP가 차단된다(2026-08 전례). yfinance KR 지수는 값 자체는 맞지만 다음이 불안정했다
(2026-11-12 07:57 측정, ^KS11·^KQ11):
  - 일봉 마지막 날이 사라졌다 나타난다 — 전날 저녁 있던 11/11 일봉이 다음 날 아침 없었다
  - 1분봉이 14:59에서 끊긴다 — 장 마지막 30분·종가 단일가가 없다
  - fast_info.previous_close가 실제 전일 종가와 다르다(5250.44 vs 5239.13)
  - fast_info.last_price만 배치(pykrx) 종가와 정확히 일치했다
그래서 현재가 = fast_info.last_price, 세션일 = 1분봉 마지막 봉의 거래소 날짜,
등락률 기준 = 배치 스냅샷의 확정 종가로 한다. 배치에 이미 있는 날짜는 확정값을 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))

# 지수명 → (yfinance 심볼, 거래소 시간대)
INDEX_SYMBOLS = {
    'KOSPI':  ('^KS11', 'Asia/Seoul'),
    'KOSDAQ': ('^KQ11', 'Asia/Seoul'),
    'NASDAQ': ('^IXIC', 'America/New_York'),
}


@dataclass(frozen=True)
class LiveQuote:
    price: float
    session_date: date      # 거래소 기준 세션일 — 장 시작 전에는 직전 세션
    fetched_at: datetime    # 조회 시각 (KST)


@dataclass(frozen=True)
class IndexDisplay:
    value: float
    delta_pct: float
    live: bool              # True = 배치에 없는 세션의 실시간(잠정) 값
    fetched_at: datetime | None = None


def fetch_live_quote(name: str, yf_module=None) -> LiveQuote | None:
    """지수 현재가. 실패하면 None — 패널은 배치 확정값으로 그린다."""
    if yf_module is None:
        import yfinance as yf_module
    symbol, tz = INDEX_SYMBOLS[name]
    try:
        bars = yf_module.download(symbol, period='1d', interval='1m',
                                  progress=False, auto_adjust=False)
        if bars is None or bars.empty:
            return None
        last_ts = pd.Timestamp(bars.index[-1])
        if last_ts.tzinfo is None:
            last_ts = last_ts.tz_localize('UTC')
        price = float(yf_module.Ticker(symbol).fast_info.last_price)
        if not price > 0:
            return None
        return LiveQuote(price=price,
                         session_date=last_ts.tz_convert(tz).date(),
                         fetched_at=datetime.now(KST))
    except Exception:
        return None


def display_values(confirmed: pd.DataFrame, quote: LiveQuote | None) -> IndexDisplay:
    """패널에 표시할 현재가·등락률.

    실시간 세션이 배치 마지막 날보다 새로우면 실시간 값(등락률은 배치 마지막 종가 대비),
    아니면 배치 확정값(마지막 두 종가). confirmed는 최소 2행이어야 한다.
    """
    last = float(confirmed['Close'].iloc[-1])
    if quote is not None and quote.session_date > pd.Timestamp(confirmed.index[-1]).date():
        return IndexDisplay(quote.price, (quote.price / last - 1) * 100, True, quote.fetched_at)
    prev = float(confirmed['Close'].iloc[-2])
    return IndexDisplay(last, (last / prev - 1) * 100, False)
