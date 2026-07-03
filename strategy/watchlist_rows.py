"""워치리스트 행 계산 — Streamlit 무관 순수 계산 레이어.

fetch/get_name/sectors_fn을 주입받으므로 테스트·배치 실행이 가능하고,
asof를 주면 그 날짜까지의 데이터만으로 계산해 과거 시점(DAY3/DAY4 기준
추가 후보 등)을 결정적으로 재현한다.
"""
import pandas as pd

from data.fetcher import fetch_daily, get_stock_name
from data.sector import get_sectors
from strategy.indicators import calc_pct_from_52w_high
from strategy.rs_correction import calc_correction_rs

ADR_MIN = {'KR': 2.0, 'US': 4.0}
FETCH_DAYS = 380   # 52주(252거래일) 고점 계산에 필요한 달력일 여유

_EMPTY_RS = {
    'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0, 'excess_adr': 0.0,
    'lead_days': 0, 'ma_score': 0,
    'vol_ratio': 0.0, 'candle_ratio': 0.0,
}


def adr_min_for(market: str) -> float:
    return ADR_MIN['KR'] if market.startswith('KR') else ADR_MIN['US']


def slice_asof(df: pd.DataFrame, asof) -> pd.DataFrame:
    """asof 날짜까지의 행만 남김. None이면 그대로."""
    if asof is None or df.empty:
        return df
    return df[df.index <= pd.Timestamp(asof)]


def ma_position(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> tuple[str, int]:
    """지수가 이탈한 이평선 기준, 종목 위/아래 텍스트 반환. (text, above_count)"""
    if index_df.empty or len(index_df) < 50:
        return '', 0

    idx_close = float(index_df['Close'].iloc[-1])
    stk_close = float(stock_df['Close'].iloc[-1])

    def _ema(df, n): return float(df['Close'].ewm(span=n, adjust=False).mean().iloc[-1])
    def _sma(df, n): return float(df['Close'].rolling(n).mean().iloc[-1]) if len(df) >= n else float('nan')

    levels = [
        ('EMA21',  _ema(index_df, 21),  _ema(stock_df, 21)),
        ('SMA50',  _sma(index_df, 50),  _sma(stock_df, 50)),
        ('SMA150', _sma(index_df, 150), _sma(stock_df, 150)),
        ('SMA200', _sma(index_df, 200), _sma(stock_df, 200)),
    ]

    parts = []
    above_count = 0
    for name, idx_ma, stk_ma in levels:
        if pd.isna(idx_ma) or pd.isna(stk_ma):
            continue
        if idx_close < idx_ma:  # 지수가 이 이평선 아래 (이탈)
            if stk_close > stk_ma:
                parts.append(f'{name}위')
                above_count += 1
            else:
                parts.append(f'{name}아래')

    return (' · '.join(parts) if parts else '지수정상'), above_count


def build_rows(
    tickers: list,
    market: str,
    index_df: pd.DataFrame,
    correction_start=None,
    jjin_date=None,
    asof=None,
    fetch=fetch_daily,
    get_name=get_stock_name,
    sectors_fn=get_sectors,
) -> dict:
    """워치리스트 행 계산. 반환: {'rows': [...], 'adr_skipped': int}"""
    index_df         = slice_asof(index_df, asof)
    correction_start = pd.Timestamp(correction_start) if correction_start is not None else None
    jjin_date        = pd.Timestamp(jjin_date)        if jjin_date        is not None else None

    adr_min     = adr_min_for(market)
    stock_cache = {}
    adr_skipped = 0

    for ticker in tickers:
        try:
            df = slice_asof(fetch(ticker, market=market, days=FETCH_DAYS), asof)
            if df.empty or len(df) < 25:
                continue
            adr = float(((df['High'] - df['Low']) / df['Close']).iloc[-20:].mean() * 100)
            if adr < adr_min:
                adr_skipped += 1
                continue
            stock_cache[ticker] = (df, round(adr, 2))
        except Exception:
            continue

    sectors = sectors_fn(list(stock_cache.keys()), market)
    rows    = []

    for ticker, (df, adr_val) in stock_cache.items():
        try:
            last_close = float(df['Close'].iloc[-1])
            prev_close = float(df['Close'].iloc[-2])
            change_pct = (last_close - prev_close) / prev_close * 100
            name       = get_name(ticker, market)

            if correction_start is not None and not index_df.empty:
                rs = calc_correction_rs(df, index_df, correction_start, jjin_date)
            else:
                rs = dict(_EMPTY_RS)

            ma_text, ma_above = ma_position(df, index_df)
            rows.append({
                'Ticker':      ticker,
                '종목명':      name,
                '섹터':        sectors.get(ticker, '기타'),
                'ADR':         adr_val,
                'Close':       round(last_close, 2),
                '등락%':       round(change_pct, 2),
                '고점대비%':   calc_pct_from_52w_high(df),
                '저점선행':    rs['lead_days'],
                '조정RS%':     rs['excess_pct'],
                'RS/ADR':      rs['excess_adr'],
                '이평선위치':  ma_text,
                'ma_above_count': ma_above,
                '거래량비%':   round(rs['vol_ratio'] * 100, 0),
                '양봉비%':     round(rs['candle_ratio'] * 100, 0),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: (
        -(r['RS/ADR'] or 0),
        -r['ma_above_count'],
        -(r['거래량비%'] or 0),
        (r['고점대비%'] or 0),   # 고점대비% 높을수록(덜 빠진) 우선 → 음수라 오름차순이 유리
    ))
    return {'rows': rows, 'adr_skipped': adr_skipped}
