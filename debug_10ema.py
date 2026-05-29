from data.fetcher import fetch_daily
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope
from strategy.indicators import calc_ema
import numpy as np

tickers = [
    ('094940','KR_KOSDAQ'), ('418420','KR_KOSDAQ'), ('064400','KR_KOSPI'),
    ('012330','KR_KOSPI'), ('254490','KR_KOSDAQ'), ('028260','KR_KOSPI'),
    ('096530','KR_KOSDAQ'), ('203650','KR_KOSDAQ'),
]

for t, market in tickers:
    df = fetch_daily(t, market=market, days=300)
    if df.empty:
        print(f'{t}: 데이터 없음\n'); continue

    pivot = find_pivot_candle(df)
    case  = classify_case(df, pivot)
    close = float(df['Close'].iloc[-1])

    if pivot:
        since = df[df.index > pivot['date']]
        min_low   = float(since['Low'].min())   if not since.empty else 0
        min_close = float(since['Close'].min()) if not since.empty else 0
        ever_low_breach   = min_low   < pivot['midline']
        ever_close_breach = min_close < pivot['midline']
        print(f'[{t}] {case}  close={close:.0f}')
        print(f'  pivot={pivot["date"].date()}  mid={pivot["midline"]:.0f}  high={pivot["high"]:.0f}')
        print(f'  기준봉후 최저Low={min_low:.0f}(이탈={ever_low_breach})  최저Close={min_close:.0f}(이탈={ever_close_breach})')
    else:
        print(f'[{t}] {case}  close={close:.0f}')
    print()
