#!/usr/bin/env python3
"""미장 10EMA 고변동성 성장주 유니버스 빌드.

NASDAQ 전 종목 중 ADR > 5% · 평균 거래량 > 50만주 · 달러 유동성 조건을 충족하는
종목 목록을 생성해 data/saved/us_10ema.tickers에 저장한다.
GitHub Actions 주간 cron에서 실행 (매 일요일).

사용법: python scripts/build_us_growth_universe.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import yfinance as yf

SAVE_PATH  = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'saved' / 'us_10ema.tickers'
CHUNK_SIZE = 200   # yfinance 배치 크기

MIN_ADR        = 5.0           # ADR 5%+
MIN_AVG_VOL    = 1_000_000     # 평균 거래량 100만주+ (TradingView 스크리너 기준)
MIN_DOLLAR_VOL = 30_000_000    # 가격 × 거래량 3000만 달러+ (TradingView 스크리너 기준)
MIN_PRICE      = 10.0          # 주가 10달러 미만 제외 (페니주 필터)
MAX_TICKERS    = 600           # 10EMA 스캔 속도 제한


def _clean_symbol(s: str) -> bool:
    s = str(s).strip()
    return bool(s) and s.isalpha() and 1 <= len(s) <= 5


def _get_candidate_tickers() -> list:
    """FDR로 NASDAQ 전 종목 심볼 가져오기."""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing('NASDAQ')
        if df is not None and not df.empty:
            sym_col = next((c for c in df.columns if c in ('Symbol', 'Code', 'Ticker')), df.columns[0])
            symbols = [str(s).strip() for s in df[sym_col].tolist()]
            symbols = [s for s in symbols if _clean_symbol(s)]
            print(f'[info] FDR NASDAQ 후보: {len(symbols)}개')
            return symbols
    except Exception as e:
        print(f'[warn] FDR 실패: {e}')

    # fallback: 알려진 고성장 NASDAQ 종목
    return [
        'NVDA', 'AMD', 'MU', 'MRVL', 'ARM', 'SMCI', 'AVGO', 'QCOM', 'AMAT', 'LRCX', 'KLAC',
        'CRWD', 'NET', 'DDOG', 'ZS', 'SNOW', 'PLTR', 'APP', 'FTNT', 'PANW', 'S', 'OKTA',
        'AXON', 'RDDT', 'COIN', 'HOOD', 'MELI', 'SE', 'GRAB', 'NU', 'CAVA', 'DUOL',
        'MRNA', 'BNTX', 'RXRX', 'VERA', 'RVMD',
        'VRT', 'ACHR', 'LUNR', 'RKLB', 'ASTS',
        'SPOT', 'RBLX', 'TTWO', 'EA', 'PINS', 'SNAP',
        'SHOP', 'DASH', 'RIVN', 'LCID',
        'AEHR', 'WOLF', 'ONTO', 'ACLS', 'ENTG', 'COHR', 'LITE', 'SNDK', 'STX', 'WDC',
    ]


def _filter_by_technicals(symbols: list) -> list:
    """ADR, 거래량, 달러유동성 기준 필터링 (yfinance 배치 다운로드)."""
    passed = []
    total  = len(symbols)

    for i in range(0, total, CHUNK_SIZE):
        chunk = symbols[i:i + CHUNK_SIZE]
        print(f'[info] 배치 {i//CHUNK_SIZE + 1}/{(total + CHUNK_SIZE - 1)//CHUNK_SIZE} '
              f'({i+1}~{min(i+len(chunk), total)}/{total})')
        try:
            df = yf.download(
                chunk, period='1mo',
                auto_adjust=True, progress=False,
                group_by='ticker', threads=True,
            )
        except Exception as e:
            print(f'  [warn] 배치 다운로드 실패: {e}')
            continue

        for ticker in chunk:
            try:
                if len(chunk) == 1:
                    tk_df = df
                else:
                    if ticker not in df.columns.get_level_values(0):
                        continue
                    tk_df = df[ticker]

                tk_df = tk_df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
                if len(tk_df) < 15:
                    continue

                adr = float(((tk_df['High'] - tk_df['Low']) / tk_df['Close']).mean() * 100)
                avg_vol = float(tk_df['Volume'].mean())
                avg_price = float(tk_df['Close'].mean())
                dollar_vol = avg_price * avg_vol

                if (adr >= MIN_ADR and avg_vol >= MIN_AVG_VOL
                        and dollar_vol >= MIN_DOLLAR_VOL and avg_price >= MIN_PRICE):
                    passed.append((ticker, round(adr, 1), int(avg_vol)))
            except Exception:
                continue

    # ADR 내림차순 정렬
    passed.sort(key=lambda x: x[1], reverse=True)
    print(f'[info] 기술적 필터 통과: {len(passed)}개 (ADR {MIN_ADR}%+, 거래량 {MIN_AVG_VOL//1000}K+)')
    return [t for t, _, _ in passed]


def build() -> list:
    candidates = _get_candidate_tickers()
    if not candidates:
        print('[ERROR] 후보 종목 없음')
        return []

    filtered = _filter_by_technicals(candidates)
    if not filtered:
        print('[ERROR] 필터 통과 종목 없음')
        return []

    if len(filtered) > MAX_TICKERS:
        filtered = filtered[:MAX_TICKERS]
        print(f'[info] 최대 {MAX_TICKERS}개로 제한')

    print(f'[info] 최종 유니버스: {len(filtered)}개')
    return filtered


def main() -> int:
    tickers = build()
    if not tickers:
        print('[FAIL] 유니버스 빌드 실패 — 기존 파일 유지')
        return 1

    SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAVE_PATH.write_text('\n'.join(tickers), encoding='utf-8')
    print(f'[OK] {SAVE_PATH} 저장 완료 ({len(tickers)}개)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
