import pandas as pd
import streamlit as st
from datetime import date, timedelta


def _prev_weekday(d: date, max_tries: int = 10) -> str:
    for _ in range(max_tries):
        if d.weekday() < 5:
            return d.strftime('%Y%m%d')
        d -= timedelta(days=1)
    return date.today().strftime('%Y%m%d')


@st.cache_data(ttl=86400)
def get_kr_universe(market: str, top_n: int = 200) -> list:
    """pykrx 시가총액 상위 N개 티커 반환 (24h 캐시)."""
    from pykrx import stock as pykrx_stock
    mkt = 'KOSPI' if market == 'KR_KOSPI' else 'KOSDAQ'

    d = date.today()
    for _ in range(15):
        if d.weekday() >= 5:
            d -= timedelta(days=1)
            continue
        try:
            day_str = d.strftime('%Y%m%d')
            df = pykrx_stock.get_market_cap(day_str, market=mkt)
            if df is not None and not df.empty:
                cap_col = next((c for c in df.columns if '시가' in c), None)
                if cap_col:
                    return df.nlargest(top_n, cap_col).index.tolist()
                return df.index[:top_n].tolist()
        except Exception:
            pass
        d -= timedelta(days=1)

    # fallback 1: 전체 티커 목록 상위 N개
    try:
        day_str = _prev_weekday(date.today())
        tickers = pykrx_stock.get_market_ticker_list(day_str, market=mkt)
        if tickers:
            return list(tickers)[:top_n]
    except Exception:
        pass

    # fallback 2: FinanceDataReader 상장 종목 목록 (시총순 정렬 가능 시)
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing(mkt)
        if df is not None and not df.empty:
            cap_col = next((c for c in df.columns if 'Cap' in c or '시가총액' in c), None)
            if cap_col:
                df = df.sort_values(cap_col, ascending=False)
            code_col = next((c for c in df.columns if c in ('Code', 'Symbol', '종목코드')), df.columns[0])
            codes = df[code_col].astype(str).str.zfill(6).tolist()
            if codes:
                return codes[:top_n]
    except Exception:
        pass

    # fallback 3: 하드코딩 주요 종목 (최후 수단)
    return _KOSPI_FALLBACK[:top_n] if mkt == 'KOSPI' else _KOSDAQ_FALLBACK[:top_n]


# pykrx 완전 실패 시 하드코딩 fallback (시총 상위 종목)
_KOSPI_FALLBACK = [
    '005930', '000660', '207940', '373220', '005380', '005490', '000270', '006400',
    '105560', '055550', '051910', '035720', '035420', '068270', '012330', '017670',
    '030200', '015760', '032830', '009830', '003550', '066570', '096770', '010950',
    '034730', '011200', '028050', '003490', '000810', '033780', '086790', '267250',
    '001570', '000100', '012450', '011170', '010130', '090430', '001040', '000720',
    '009540', '010060', '161390', '034020', '047050', '011780', '003240', '000080',
    '097950', '018880', '006800', '009150', '002380', '000990', '079550', '024110',
    '139480', '259960', '316140', '402340', '000120', '042660', '004020', '010140',
]

_KOSDAQ_FALLBACK = [
    '293490', '263750', '145020', '196170', '028300', '140860', '096530', '086520',
    '041510', '112040', '091990', '039030', '033600', '046080', '228760', '054620',
    '214150', '200130', '078600', '036540', '032980', '082640', '033180', '246690',
    '357780', '069510', '122900', '215600', '041960', '226950', '095700', '058470',
    '067160', '257720', '066970', '080530', '240810', '048410', '064350', '950130',
    '251270', '347700', '336370', '101000', '036830', '035900', '039440', '192400',
]

_SP500_URL = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

# Wikipedia 접근 실패 시 사용할 주요 미국 주식 목록
_US_FALLBACK = [
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'GOOG', 'META', 'TSLA', 'AVGO', 'ORCL',
    'CRM', 'ADBE', 'AMD', 'QCOM', 'MU', 'INTC', 'AMAT', 'LRCX', 'KLAC', 'MRVL',
    'ON', 'TXN', 'ADI', 'MCHP', 'NXPI', 'MPWR', 'ENTG', 'ACLS', 'ONTO', 'WOLF',
    'SMCI', 'DELL', 'HPE', 'IBM', 'CSCO', 'NOK', 'ERIC',
    'NOW', 'SNOW', 'DDOG', 'NET', 'ZS', 'CRWD', 'OKTA', 'PANW', 'FTNT', 'S',
    'PLTR', 'SHOP', 'HUBS', 'VEEV', 'TWLO', 'MDB', 'BILL', 'PAYC', 'PCTY',
    'INTU', 'SNPS', 'CDNS', 'ANSS', 'PTC', 'EPAM', 'GLOB',
    'NFLX', 'SPOT', 'PINS', 'SNAP', 'RDDT', 'RBLX', 'TTWO', 'EA',
    'UBER', 'LYFT', 'ABNB', 'BKNG', 'EXPE', 'DASH',
    'JPM', 'BAC', 'GS', 'MS', 'V', 'MA', 'PYPL', 'COIN', 'SQ',
    'LLY', 'ABBV', 'PFE', 'MRK', 'AMGN', 'GILD', 'REGN', 'VRTX', 'BIIB',
    'UNH', 'CI', 'HUM', 'MRNA', 'ISRG', 'DXCM', 'IDXX', 'TMO', 'DHR',
    'TSLA', 'GM', 'F', 'RIVN',
    'ENPH', 'FSLR', 'NEE', 'CEG',
    'CAT', 'DE', 'HON', 'GE', 'MMM', 'BA', 'RTX', 'LMT', 'NOC', 'GD',
    'XOM', 'CVX', 'COP', 'SLB',
    'AMT', 'PLD', 'EQIX', 'CCI',
    'APP', 'TTD', 'MGNI', 'PUBM',
    'VRT', 'ETN', 'PWR', 'ACHR',
    'ARM', 'ASML', 'TSM',
    'COST', 'WMT', 'TGT', 'HD', 'LOW',
]


@st.cache_data(ttl=86400)
def get_us_universe() -> list:
    """S&P500 티커 목록 반환 (Wikipedia → FinanceDataReader → fallback, 24h 캐시)."""
    tickers: set = set()

    # 1순위: Wikipedia S&P500 목록
    try:
        df = pd.read_html(_SP500_URL, attrs={'id': 'constituents'})[0]
        for t in df['Symbol'].tolist():
            tickers.add(str(t).replace('.', '-'))
    except Exception:
        pass

    # 2순위: FinanceDataReader S&P500 (Wikipedia 실패 시)
    if len(tickers) < 50:
        try:
            import FinanceDataReader as fdr
            df = fdr.StockListing('S&P500')
            if df is not None and not df.empty:
                code_col = next((c for c in df.columns if c in ('Symbol', 'Code')), df.columns[0])
                for t in df[code_col].tolist():
                    tickers.add(str(t).replace('.', '-'))
        except Exception:
            pass

    # 추가 주요 NASDAQ 성장주 (S&P500 외)
    _extra = ['PLTR', 'APP', 'RDDT', 'HOOD', 'GRAB', 'SE', 'MELI', 'NU',
              'CAVA', 'DUOL', 'ARM', 'SMCI', 'VRT', 'COIN', 'RBLX', 'SNAP',
              'DASH', 'LYFT', 'RIVN', 'MRNA', 'BNTX', 'CRWD', 'NET', 'DDOG',
              'SNOW', 'ZS', 'S', 'TTWO', 'SPOT', 'PINS']
    tickers.update(_extra)

    # 3순위: 하드코딩 (최후 수단)
    if len(tickers) < 50:
        tickers.update(_US_FALLBACK)

    return sorted(tickers)
