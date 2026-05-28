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
    return df[needed]   # dropna는 호출부에서 처리


def _patch_kr_today(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance 마지막 행 Close가 NaN이면 pykrx로 채움 (자정 이후 당일 데이터 공백 대응)."""
    if df.empty or not pd.isna(df['Close'].iloc[-1]):
        return df
    last_date = df.index[-1]
    try:
        from pykrx import stock as pykrx_stock
        date_str = last_date.strftime('%Y%m%d')
        pk = pykrx_stock.get_market_ohlcv_by_date(date_str, date_str, ticker)
        if not pk.empty:
            df.loc[last_date, 'Open']   = float(pk['시가'].iloc[0])
            df.loc[last_date, 'High']   = float(pk['고가'].iloc[0])
            df.loc[last_date, 'Low']    = float(pk['저가'].iloc[0])
            df.loc[last_date, 'Close']  = float(pk['종가'].iloc[0])
            df.loc[last_date, 'Volume'] = float(pk['거래량'].iloc[0])
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
        return df.dropna(subset=['Close'])
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
    """한국 지수 마지막 행 Close NaN → fast_info.last_price로 채움."""
    if df.empty or not pd.isna(df['Close'].iloc[-1]):
        return df
    try:
        last_price = yf.Ticker(yf_ticker).fast_info.last_price
        if last_price and last_price > 0:
            df.loc[df.index[-1], 'Close'] = float(last_price)
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
    return df.dropna(subset=['Close'])


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
