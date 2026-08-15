#!/usr/bin/env python3
"""국장 10EMA 고변동성 성장주 유니버스 빌드.

KOSDAQ / KOSPI 전종목 중 ADR 6%+, 거래량 30만주+, 거래대금 100억원+ 종목을
생성해 data/saved/kosdaq_10ema.tickers / kospi_10ema.tickers 에 저장한다.
GitHub Actions 주간 cron에서 실행 (매 일요일).

사용법: python scripts/build_kr_10ema_universe.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import yfinance as yf

SAVED_DIR = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'saved'

MIN_ADR         = 6.0            # ADR 6%+
MIN_AVG_VOL     = 300_000        # 평균 거래량 30만주+
MIN_DOLLAR_VOL  = 10_000_000_000 # 거래대금 100억 KRW+
MIN_MARCAP      = 100_000_000_000 # 시총 1000억 KRW+ (1차 필터)
MIN_PRICE_KRW   = 2_000          # 주가 2,000원 미만 제외
CHUNK_SIZE      = 100
MAX_TICKERS     = 300


def _get_candidates_fdr(fdr_market: str, yf_suffix: str) -> list:
    """FDR 스냅샷으로 시총·거래대금 1차 필터 후 (ticker_6digit, yf_ticker) 반환."""
    import FinanceDataReader as fdr
    df = fdr.StockListing(fdr_market)
    if df is None or df.empty:
        return []

    code_col   = next((c for c in df.columns if c in ('Code', 'Symbol')), None)
    marcap_col = next((c for c in df.columns if 'Marcap' in c or 'marcap' in c), None)
    amount_col = next((c for c in df.columns if 'Amount' in c), None)

    if code_col is None:
        return []

    df[code_col] = df[code_col].astype(str).str.zfill(6)
    # 6자리 숫자 보통주만 (우선주·권리주 제외)
    df = df[df[code_col].str.match(r'^\d{6}$')]

    if marcap_col:
        df[marcap_col] = pd.to_numeric(df[marcap_col], errors='coerce').fillna(0)
        df = df[df[marcap_col] >= MIN_MARCAP]

    if amount_col:
        df[amount_col] = pd.to_numeric(df[amount_col], errors='coerce').fillna(0)
        df = df[df[amount_col] >= MIN_DOLLAR_VOL * 0.05]  # 느슨하게 — 거래대금의 5% 이상인 날

    codes = df[code_col].tolist()
    return [(c, c + yf_suffix) for c in codes]


def _filter_by_adr(candidates: list) -> list:
    """yfinance 배치 다운로드로 ADR/거래량 최종 필터링."""
    if not candidates:
        return []

    base_tickers = [c[0] for c in candidates]
    yf_tickers   = [c[1] for c in candidates]
    passed = []
    total  = len(yf_tickers)

    for i in range(0, total, CHUNK_SIZE):
        chunk = yf_tickers[i:i + CHUNK_SIZE]
        bases = base_tickers[i:i + CHUNK_SIZE]
        print(f'  배치 {i//CHUNK_SIZE + 1}/{(total + CHUNK_SIZE - 1)//CHUNK_SIZE} '
              f'({i+1}~{min(i+len(chunk), total)}/{total})')
        try:
            df = yf.download(
                chunk, period='1mo',
                auto_adjust=True, progress=False,
                group_by='ticker', threads=True,
            )
        except Exception as e:
            print(f'  [warn] 배치 실패: {e}')
            continue

        for yf_tk, base in zip(chunk, bases):
            try:
                tk_df = df if len(chunk) == 1 else (
                    df[yf_tk] if yf_tk in df.columns.get_level_values(0) else None
                )
                if tk_df is None:
                    continue
                tk_df = tk_df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
                if len(tk_df) < 10:
                    continue

                avg_price = float(tk_df['Close'].mean())
                if avg_price < MIN_PRICE_KRW:
                    continue

                adr      = float(((tk_df['High'] - tk_df['Low']) / tk_df['Close']).mean() * 100)
                avg_vol  = float(tk_df['Volume'].mean())
                dollar_v = avg_price * avg_vol

                if adr >= MIN_ADR and avg_vol >= MIN_AVG_VOL and dollar_v >= MIN_DOLLAR_VOL:
                    passed.append((base, round(adr, 1)))
            except Exception:
                continue

    passed.sort(key=lambda x: x[1], reverse=True)
    return passed


def build_market(fdr_market: str, yf_suffix: str) -> list:
    print(f'[info] {fdr_market} 1차 필터 (시총·거래대금)...')
    candidates = _get_candidates_fdr(fdr_market, yf_suffix)
    if not candidates:
        print(f'[ERROR] {fdr_market} 후보 종목 없음')
        return []
    print(f'[info] {fdr_market} 1차 후보: {len(candidates)}개 → ADR 계산 시작')

    passed = _filter_by_adr(candidates)
    tickers = [t for t, _ in passed]

    if len(tickers) > MAX_TICKERS:
        tickers = tickers[:MAX_TICKERS]

    print(f'[info] {fdr_market} 최종: {len(tickers)}개 '
          f'(ADR {MIN_ADR}%+, 거래량 {MIN_AVG_VOL//1000:.0f}K+, '
          f'거래대금 {MIN_DOLLAR_VOL//1_000_000_000:.0f}B KRW+)')
    return tickers


def main() -> int:
    ok = True
    for fdr_market, yf_suffix, filename in [
        ('KOSDAQ', '.KQ', 'kosdaq_10ema.tickers'),
        ('KOSPI',  '.KS', 'kospi_10ema.tickers'),
    ]:
        tickers = build_market(fdr_market, yf_suffix)
        if not tickers:
            print(f'[FAIL] {fdr_market} 빌드 실패 — 기존 파일 유지')
            ok = False
            continue
        path = SAVED_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(tickers), encoding='utf-8')
        print(f'[OK] {path} 저장 완료 ({len(tickers)}개)\n')

    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
