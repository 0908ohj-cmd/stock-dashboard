from functools import lru_cache

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta


def _prev_weekday(d) -> 'datetime.date':
    """d 직전의 평일 반환. 주말이면 지난 금요일 — 주말·월요일마다 KRX에 없는
    날짜(일요일 등)를 조회하는 헛 호출을 막는다."""
    return pd.Timestamp(np.busday_offset(np.datetime64(d, 'D'), -1, roll='forward')).date()

INDICES = {
    'KOSPI':  '^KS11',
    'KOSDAQ': '^KQ11',
    'NASDAQ': '^IXIC',
    'QQQ':    'QQQ',
}


def _download(ticker: str, start, end, interval: str = '1d') -> pd.DataFrame:
    df = yf.download(ticker, start=start, end=end, interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
    return df[needed]   # dropna는 호출부에서 처리


def _patch_kr_today(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance 마지막 행 Close가 NaN이거나 전거래일 행이 아예 없으면 pykrx로 채움."""
    if df.empty:
        return df

    yesterday = _prev_weekday(datetime.today().date())
    last_date = df.index[-1].date()
    last_close_nan = pd.isna(df['Close'].iloc[-1])

    # NaN도 없고 어제 이후 데이터면 패치 불필요
    if not last_close_nan and last_date >= yesterday:
        return df

    try:
        from pykrx import stock as pykrx_stock
        fetch_date = last_date if last_close_nan else yesterday
        date_str = fetch_date.strftime('%Y%m%d')
        pk = pykrx_stock.get_market_ohlcv_by_date(date_str, date_str, ticker)
        if not pk.empty:
            ts = pd.Timestamp(fetch_date)
            df.loc[ts, 'Open']   = float(pk['시가'].iloc[0])
            df.loc[ts, 'High']   = float(pk['고가'].iloc[0])
            df.loc[ts, 'Low']    = float(pk['저가'].iloc[0])
            df.loc[ts, 'Close']  = float(pk['종가'].iloc[0])
            df.loc[ts, 'Volume'] = float(pk['거래량'].iloc[0])
            df = df.sort_index()
    except Exception:
        pass
    return df


def fetch_daily(ticker: str, market: str = 'US', days: int = 300) -> pd.DataFrame:
    end = datetime.today() + timedelta(days=1)   # yfinance end exclusive, KST 자정 이슈 방지
    start = end - timedelta(days=days + 1)
    if market.startswith('KR'):
        suffix = '.KS' if 'KOSPI' in market else '.KQ'
        df = _download(ticker + suffix, start, end)
        if df.empty and 'KOSPI' in market:
            df = _download(ticker + '.KQ', start, end)
        df = _patch_kr_today(df, ticker)
        return df.dropna(subset=['Close']) if 'Close' in df.columns else df
    return _download(ticker, start, end).dropna()


def fetch_intraday(ticker: str, market: str = 'US') -> pd.DataFrame:
    if market.startswith('KR'):
        suffix = '.KS' if 'KOSPI' in market else '.KQ'
        yf_ticker = ticker + suffix
    else:
        yf_ticker = ticker
    df = yf.download(yf_ticker, period='5d', interval='5m',
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
    return df[needed].dropna()


def _patch_kr_index_today(df: pd.DataFrame, yf_ticker: str) -> pd.DataFrame:
    """한국 지수 마지막 행 Close NaN이거나 전거래일 행이 없으면 fast_info로 채움."""
    if df.empty:
        return df

    yesterday = _prev_weekday(datetime.today().date())
    last_date = df.index[-1].date()
    last_close_nan = pd.isna(df['Close'].iloc[-1])

    if not last_close_nan and last_date >= yesterday:
        return df

    try:
        last_price = yf.Ticker(yf_ticker).fast_info.last_price
        if last_price and last_price > 0:
            ts = pd.Timestamp(last_date if last_close_nan else yesterday)
            df.loc[ts, 'Close'] = float(last_price)
            df = df.sort_index()
    except Exception:
        pass
    return df


_KR_INDEX_PYKRX = {'^KS11': '1001', '^KQ11': '2001'}


_KR_INDEX_FDR = {'^KS11': 'KOSPI', '^KQ11': 'KOSDAQ'}


def _patch_kr_index_ohlc(df: pd.DataFrame, yf_ticker: str) -> pd.DataFrame:
    """yfinance가 O=H=L=C로 반환한 행(OHLC 불완전)을 pykrx → FDR 순서로 교체."""
    if df.empty:
        return df
    pykrx_code = _KR_INDEX_PYKRX.get(yf_ticker)
    fdr_name   = _KR_INDEX_FDR.get(yf_ticker)
    if not pykrx_code:
        return df

    def _bad_dates(d: pd.DataFrame):
        mask = (d['Open'] == d['Close']) & (d['High'] == d['Close']) & (d['Low'] == d['Close'])
        return d.index[mask]

    bad = _bad_dates(df)
    if bad.empty:
        return df

    # 1순위: pykrx
    try:
        from pykrx import stock as pykrx_stock
        for ts in bad:
            date_str = ts.strftime('%Y%m%d')
            pk = pykrx_stock.get_index_ohlcv_by_date(date_str, date_str, pykrx_code)
            if pk.empty:
                continue
            df.loc[ts, 'Open']  = float(pk['시가'].iloc[0])
            df.loc[ts, 'High']  = float(pk['고가'].iloc[0])
            df.loc[ts, 'Low']   = float(pk['저가'].iloc[0])
            df.loc[ts, 'Close'] = float(pk['종가'].iloc[0])
    except Exception:
        pass

    # 2순위: FDR (pykrx 실패 시 남은 불량 행 처리)
    bad = _bad_dates(df)
    if bad.empty or not fdr_name:
        return df
    try:
        import FinanceDataReader as fdr
        start_str = bad[0].strftime('%Y-%m-%d')
        end_str   = (bad[-1] + timedelta(days=2)).strftime('%Y-%m-%d')
        fdr_df    = fdr.DataReader(fdr_name, start_str, end_str)
        if isinstance(fdr_df.columns, pd.MultiIndex):
            fdr_df.columns = fdr_df.columns.get_level_values(0)
        fdr_df.index = pd.to_datetime(fdr_df.index).normalize()
        for ts in bad:
            row = fdr_df[fdr_df.index == ts]
            if row.empty:
                continue
            for col_fdr, col_df in [('Open','Open'),('High','High'),('Low','Low'),('Close','Close')]:
                if col_fdr in row.columns:
                    df.loc[ts, col_df] = float(row[col_fdr].iloc[0])
    except Exception:
        pass

    return df


def _patch_us_index_ohlc(df: pd.DataFrame, yf_ticker: str) -> pd.DataFrame:
    """US 지수 O=H=L=C 불완전 행을 yf.Ticker.history로 재시도."""
    if df.empty:
        return df
    bad_mask = (
        (df['Open'] == df['Close']) &
        (df['High'] == df['Close']) &
        (df['Low']  == df['Close'])
    )
    bad_dates = df.index[bad_mask]
    if bad_dates.empty:
        return df
    try:
        t = yf.Ticker(yf_ticker)
        for ts in bad_dates:
            start_s = ts.strftime('%Y-%m-%d')
            end_s   = (ts + timedelta(days=2)).strftime('%Y-%m-%d')
            hist = t.history(start=start_s, end=end_s, interval='1d', auto_adjust=True)
            if hist.empty:
                continue
            hist.index = hist.index.normalize().tz_localize(None)
            row = hist[hist.index == ts]
            if row.empty:
                continue
            for col in ['Open', 'High', 'Low', 'Close']:
                if col in row.columns:
                    df.loc[ts, col] = float(row[col].iloc[0])
    except Exception:
        pass
    return df


def fetch_index_daily(name: str, days: int = 300) -> pd.DataFrame:
    ticker = INDICES[name]
    end = datetime.today() + timedelta(days=1)   # KST 자정 이슈 방지
    start = end - timedelta(days=days + 1)
    df = _download(ticker, start, end)
    if name in ('KOSPI', 'KOSDAQ'):
        df = _patch_kr_index_today(df, ticker)
        df = _patch_kr_index_ohlc(df, ticker)
    else:
        df = _patch_us_index_ohlc(df, ticker)
    return df.dropna(subset=['Close'])


def _parse_fdr_listing(df: pd.DataFrame) -> dict:
    CODE_COLS = ['Code', 'Symbol', '종목코드', 'Ticker', 'ticker', 'symbol']
    NAME_COLS = ['Name', '종목명', '기업명', 'CompanyName', 'company_name']
    code_col = next((c for c in CODE_COLS if c in df.columns), None)
    name_col = next((c for c in NAME_COLS if c in df.columns), None)
    if not code_col or not name_col:
        return {}
    return dict(zip(df[code_col].astype(str).str.zfill(6), df[name_col].astype(str)))


# None = 미로드, {} = 로드 성공했지만 비어있음
_kr_names_map: dict | None = None


def _load_kr_names_fdr() -> dict:
    """KR 종목명 로드. 번들 JSON → FDR → pykrx 순서로 시도."""
    global _kr_names_map
    if _kr_names_map:
        return _kr_names_map

    # 1순위: 번들 JSON (Cloud 환경에서도 안정적)
    try:
        import json, pathlib
        json_path = pathlib.Path(__file__).parent / 'kr_names.json'
        if json_path.exists():
            with open(json_path, encoding='utf-8') as f:
                result = json.load(f)
            if result:
                _kr_names_map = result
                return result
    except Exception:
        pass

    # 2순위: FDR (네트워크)
    try:
        import FinanceDataReader as fdr
        result = {}
        for mkt in ['KOSPI', 'KOSDAQ']:
            try:
                result.update(_parse_fdr_listing(fdr.StockListing(mkt)))
            except Exception:
                pass
        if not result:
            result = _parse_fdr_listing(fdr.StockListing('KRX'))
        if result:
            _kr_names_map = result
        return result
    except Exception:
        return {}


_name_cache: dict = {}


def get_stock_name(ticker: str, market: str = 'US') -> str:
    """종목 이름 반환. 성공한 결과만 캐시 (실패 시 재시도)."""
    key = (ticker, market)
    if key in _name_cache:
        return _name_cache[key]

    name = None

    if market.startswith('KR'):
        # 1순위: FDR (한국어 종목명)
        kr_map = _load_kr_names_fdr()
        name = kr_map.get(ticker.zfill(6)) or kr_map.get(ticker) or None

        # 2순위: pykrx
        if not name:
            try:
                from pykrx import stock as pykrx_stock
                raw = pykrx_stock.get_market_ticker_name(ticker)
                name = raw if raw and raw != ticker else None
            except Exception:
                pass

    else:
        # US: yfinance shortName / longName
        try:
            info = yf.Ticker(ticker).info
            name = info.get('shortName') or info.get('longName') or info.get('name') or None
        except Exception:
            pass

    if name:
        _name_cache[key] = name
    return name or ticker


def fetch_intraday_for_date(
    ticker: str, target_date, market: str = 'US', days: int = 1
) -> pd.DataFrame:
    """찐반등 날 포함 이전 N 거래일 5분봉. 60일 초과 시 빈 DataFrame 반환."""
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.to_pydatetime()
    if (datetime.today() - target_date).days > 59:
        return pd.DataFrame()

    if market.startswith('KR'):
        suffix    = '.KS' if 'KOSPI' in market else '.KQ'
        yf_ticker = ticker + suffix
    else:
        yf_ticker = ticker

    end   = target_date.replace(hour=23, minute=59, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=days * 2 + 3)  # 주말 여유 포함해서 N 거래일 확보
    return _download(yf_ticker, start, end, interval='5m')


def fetch_index_intraday_for_date(name: str, target_date, days: int = 1) -> pd.DataFrame:
    """지수 찐반등 날 포함 이전 N 거래일 5분봉."""
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.to_pydatetime()
    if (datetime.today() - target_date).days > 59:
        return pd.DataFrame()

    ticker = INDICES[name]
    end    = target_date.replace(hour=23, minute=59, second=0, microsecond=0) + timedelta(days=1)
    start  = end - timedelta(days=days * 2 + 3)
    return _download(ticker, start, end, interval='5m')


def parse_tradingview_csv(uploaded_file) -> pd.DataFrame:
    """TradingView 스크리너 CSV 파싱."""
    df = pd.read_csv(uploaded_file)
    ticker_col = df.columns[0]
    df = df.rename(columns={ticker_col: 'Ticker'})
    # KRX:005930 → 005930
    df['Ticker'] = df['Ticker'].astype(str).str.split(':').str[-1]
    return df


def parse_ticker_txt(content: str) -> list:
    """
    TradingView 와치리스트 TXT 파싱.
    포맷: KRX:229200,KRX:KOSDAQ,###그룹명,KRX:200470,...
    - ###로 시작하는 그룹 라벨 제거
    - KRX: 접두사 제거
    - 숫자로 시작하는 것만 유효 티커로 취급 (지수·이름 제외)
    """
    tickers = []
    for part in content.replace('\n', ',').split(','):
        part = part.strip()
        if not part or part.startswith('###'):
            continue
        ticker = part.split(':')[-1].strip()
        if ticker:
            tickers.append(ticker)
    return tickers
