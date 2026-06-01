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
    """yfinance 마지막 행 Close가 NaN이거나 전거래일 행이 아예 없으면 pykrx로 채움."""
    if df.empty:
        return df

    yesterday = (datetime.today() - timedelta(days=1)).date()
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

    yesterday = (datetime.today() - timedelta(days=1)).date()
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


def fetch_index_daily(name: str, days: int = 300) -> pd.DataFrame:
    ticker = INDICES[name]
    end = datetime.today() + timedelta(days=1)   # KST 자정 이슈 방지
    start = end - timedelta(days=days + 1)
    df = _download(ticker, start, end)
    if name in ('KOSPI', 'KOSDAQ'):
        df = _patch_kr_index_today(df, ticker)
    return df.dropna(subset=['Close'])


_name_cache: dict = {}


def get_stock_name(ticker: str, market: str = 'US') -> str:
    """종목 이름 반환. 성공한 결과만 캐시 (실패 시 재시도)."""
    key = (ticker, market)
    if key in _name_cache:
        return _name_cache[key]

    name = None
    try:
        if market.startswith('KR'):
            from pykrx import stock as pykrx_stock
            name = pykrx_stock.get_market_ticker_name(ticker) or None
    except Exception:
        pass

    if not name:
        try:
            suffix = '.KS' if market == 'KR_KOSPI' else ('.KQ' if market == 'KR_KOSDAQ' else '')
            info = yf.Ticker(ticker + suffix).info
            name = info.get('shortName') or info.get('longName') or None
        except Exception:
            pass

    if name:
        _name_cache[key] = name
    return name or ticker


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
