import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

INDICES = {
    'KOSPI':  '^KS11',
    'KOSDAQ': '^KQ11',
    'SPY':    'SPY',
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
    return df[needed].dropna()


def fetch_daily(ticker: str, market: str = 'US', days: int = 300) -> pd.DataFrame:
    end = datetime.today()
    start = end - timedelta(days=days)
    if market.startswith('KR'):
        suffix = '.KS' if 'KOSPI' in market else '.KQ'
        df = _download(ticker + suffix, start, end)
        if df.empty and 'KOSPI' in market:
            df = _download(ticker + '.KQ', start, end)
        return df
    return _download(ticker, start, end)


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


def fetch_index_daily(name: str, days: int = 300) -> pd.DataFrame:
    ticker = INDICES[name]
    end = datetime.today()
    start = end - timedelta(days=days)
    return _download(ticker, start, end)


def get_stock_name(ticker: str, market: str = 'US') -> str:
    """종목 이름 반환. 실패 시 티커 그대로 반환."""
    try:
        if market.startswith('KR'):
            from pykrx import stock as pykrx_stock
            name = pykrx_stock.get_market_ticker_name(ticker)
            if name:
                return name
        suffix = '.KS' if market == 'KR_KOSPI' else ('.KQ' if market == 'KR_KOSDAQ' else '')
        info = yf.Ticker(ticker + suffix).info
        return info.get('shortName') or info.get('longName') or ticker
    except Exception:
        return ticker


def fetch_intraday_for_date(
    ticker: str, target_date, market: str = 'US'
) -> pd.DataFrame:
    """특정 날짜 5분봉. 60일 초과 시 빈 DataFrame 반환."""
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.to_pydatetime()
    if (datetime.today() - target_date).days > 59:
        return pd.DataFrame()

    if market.startswith('KR'):
        suffix    = '.KS' if 'KOSPI' in market else '.KQ'
        yf_ticker = ticker + suffix
    else:
        yf_ticker = ticker

    start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(days=1)
    return _download(yf_ticker, start, end, interval='5m')


def fetch_index_intraday_for_date(name: str, target_date) -> pd.DataFrame:
    """지수 특정 날짜 5분봉."""
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.to_pydatetime()
    if (datetime.today() - target_date).days > 59:
        return pd.DataFrame()

    ticker = INDICES[name]
    start  = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end    = start + timedelta(days=1)
    return _download(ticker, start, end, interval='5m')


def parse_tradingview_csv(uploaded_file) -> pd.DataFrame:
    """TradingView 스크리너 CSV 파싱."""
    df = pd.read_csv(uploaded_file)
    ticker_col = df.columns[0]
    df = df.rename(columns={ticker_col: 'Ticker'})
    # KRX:005930 → 005930
    df['Ticker'] = df['Ticker'].astype(str).str.split(':').str[-1]
    return df
