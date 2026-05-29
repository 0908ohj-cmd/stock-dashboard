# Stock Watchlist Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TradingView 스크리너 CSV를 업로드하면 추세추종 전략(DAY1/2/3)을 자동 분석해 한국/미국 와치리스트를 보여주는 Streamlit 대시보드

**Architecture:** Streamlit 단일 앱. data 레이어(yfinance/FDR)→strategy 레이어(지표/페이즈/스코어링)→ui 레이어(패널/테이블/차트) 순으로 데이터 흐름. 각 레이어는 순수 함수로 구성해 독립 테스트 가능.

**Tech Stack:** Python 3.11+, Streamlit, yfinance, FinanceDataReader, pandas, numpy, plotly

---

## File Map

| 파일 | 역할 |
|------|------|
| `app.py` | Streamlit 진입점, 사이드바 + 레이아웃 |
| `data/fetcher.py` | yfinance/FDR로 OHLCV 수집, 일봉+5분봉 |
| `strategy/indicators.py` | EMA21, SMA200, ADR, RS 계산 |
| `strategy/phases.py` | DAY1/2/3 페이즈 감지 |
| `strategy/scoring.py` | 3가지 우세 기준 스코어링 |
| `ui/index_panel.py` | 지수 현황 카드 (KOSPI/KOSDAQ/SPY/QQQ) |
| `ui/watchlist.py` | 와치리스트 테이블 (KOSPI/KOSDAQ/US 탭) |
| `ui/charts.py` | 일봉 + 5분봉 Plotly 차트 |
| `requirements.txt` | 패키지 목록 |
| `tests/test_indicators.py` | 지표 계산 단위 테스트 |
| `tests/test_phases.py` | 페이즈 감지 단위 테스트 |
| `tests/test_scoring.py` | 스코어링 단위 테스트 |

---

## Task 1: 프로젝트 셋업

**Files:**
- Create: `requirements.txt`
- Create: `data/__init__.py`
- Create: `strategy/__init__.py`
- Create: `ui/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: requirements.txt 작성**

```
streamlit>=1.35.0
yfinance>=0.2.40
FinanceDataReader>=0.9.50
pandas>=2.0.0
numpy>=1.26.0
plotly>=5.20.0
pytest>=8.0.0
```

- [ ] **Step 2: 디렉토리 + __init__ 파일 생성**

```bash
cd C:\Users\PC\stock-dashboard
mkdir data strategy ui tests
type nul > data\__init__.py
type nul > strategy\__init__.py
type nul > ui\__init__.py
type nul > tests\__init__.py
```

- [ ] **Step 3: 가상환경 생성 및 패키지 설치**

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

- [ ] **Step 4: 설치 확인**

```bash
python -c "import streamlit, yfinance, FinanceDataReader, plotly; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: git 초기화 및 커밋**

```bash
git init
echo "venv/" > .gitignore
echo "__pycache__/" >> .gitignore
echo ".pytest_cache/" >> .gitignore
git add .
git commit -m "feat: project setup"
```

---

## Task 2: 지표 계산 (indicators.py)

**Files:**
- Create: `strategy/indicators.py`
- Create: `tests/test_indicators.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_indicators.py
import pandas as pd
import numpy as np
import pytest
from strategy.indicators import calc_ema, calc_sma, calc_adr, calc_rs

def make_ohlcv(closes, highs=None, lows=None):
    n = len(closes)
    if highs is None:
        highs = [c * 1.02 for c in closes]
    if lows is None:
        lows = [c * 0.98 for c in closes]
    return pd.DataFrame({
        'Open': closes,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': [1_000_000] * n
    })

def test_calc_ema_length():
    df = make_ohlcv([100.0] * 30)
    result = calc_ema(df, period=21)
    assert len(result) == len(df)

def test_calc_ema_constant_series():
    df = make_ohlcv([100.0] * 30)
    result = calc_ema(df, period=21)
    assert abs(result.iloc[-1] - 100.0) < 0.01

def test_calc_sma_constant_series():
    df = make_ohlcv([50.0] * 210)
    result = calc_sma(df, period=200)
    assert abs(result.iloc[-1] - 50.0) < 0.01

def test_calc_adr():
    # high=102, low=98, close=100 → range=4, adr%=4%
    closes = [100.0] * 20
    highs = [102.0] * 20
    lows = [98.0] * 20
    df = make_ohlcv(closes, highs, lows)
    adr = calc_adr(df, period=14)
    assert abs(adr - 4.0) < 0.01

def test_calc_rs_positive():
    stock = make_ohlcv([100.0] + [110.0] * 20)  # +10%
    index = make_ohlcv([100.0] + [105.0] * 20)  # +5%
    rs = calc_rs(stock, index, period=20)
    assert rs > 1.0  # 주식이 지수보다 강함

def test_calc_rs_negative():
    stock = make_ohlcv([100.0] + [95.0] * 20)   # -5%
    index = make_ohlcv([100.0] + [105.0] * 20)  # +5%
    rs = calc_rs(stock, index, period=20)
    assert rs < 1.0
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_indicators.py -v
```
Expected: `ImportError` 또는 `ModuleNotFoundError`

- [ ] **Step 3: indicators.py 구현**

```python
# strategy/indicators.py
import pandas as pd
import numpy as np


def calc_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df['Close'].ewm(span=period, adjust=False).mean()


def calc_sma(df: pd.DataFrame, period: int) -> pd.Series:
    return df['Close'].rolling(window=period).mean()


def calc_adr(df: pd.DataFrame, period: int = 14) -> float:
    daily_range_pct = (df['High'] - df['Low']) / df['Close'] * 100
    return daily_range_pct.tail(period).mean()


def calc_rs(stock_df: pd.DataFrame, index_df: pd.DataFrame, period: int = 20) -> float:
    if len(stock_df) < period + 1 or len(index_df) < period + 1:
        return 1.0
    stock_return = stock_df['Close'].iloc[-1] / stock_df['Close'].iloc[-(period + 1)]
    index_return = index_df['Close'].iloc[-1] / index_df['Close'].iloc[-(period + 1)]
    if index_return == 0:
        return 1.0
    return stock_return / index_return
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_indicators.py -v
```
Expected: 5개 PASSED

- [ ] **Step 5: 커밋**

```bash
git add strategy/indicators.py tests/test_indicators.py
git commit -m "feat: technical indicators (EMA, SMA, ADR, RS)"
```

---

## Task 3: 데이터 수집 (fetcher.py)

**Files:**
- Create: `data/fetcher.py`

- [ ] **Step 1: fetcher.py 작성**

```python
# data/fetcher.py
import pandas as pd
import yfinance as yf
import FinanceDataReader as fdr
from datetime import datetime, timedelta


INDICES = {
    'KOSPI': '^KS11',
    'KOSDAQ': '^KQ11',
    'SPY': 'SPY',
    'QQQ': 'QQQ',
}

KR_MARKET_SUFFIX = '.KS'   # KOSPI
KQ_MARKET_SUFFIX = '.KQ'   # KOSDAQ


def fetch_daily(ticker: str, market: str = 'US', days: int = 300) -> pd.DataFrame:
    """일봉 OHLCV 반환. market='KR_KOSPI'|'KR_KOSDAQ'|'US'"""
    end = datetime.today()
    start = end - timedelta(days=days)

    if market.startswith('KR'):
        df = fdr.DataReader(ticker, start=start.strftime('%Y-%m-%d'))
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns=str.title)
        df.index = pd.to_datetime(df.index)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    else:
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def fetch_intraday(ticker: str, market: str = 'US') -> pd.DataFrame:
    """5분봉 OHLCV 반환 (최근 5일). 한국 주식은 yfinance KRX suffix 사용."""
    if market.startswith('KR'):
        suffix = KR_MARKET_SUFFIX if 'KOSPI' in market else KQ_MARKET_SUFFIX
        yf_ticker = ticker + suffix
    else:
        yf_ticker = ticker

    df = yf.download(yf_ticker, period='5d', interval='5m', progress=False, auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df.columns = df.columns.get_level_values(0)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def fetch_index_daily(name: str, days: int = 300) -> pd.DataFrame:
    """지수 일봉 반환. name: 'KOSPI'|'KOSDAQ'|'SPY'|'QQQ'"""
    ticker = INDICES[name]
    end = datetime.today()
    start = end - timedelta(days=days)

    if name in ('KOSPI', 'KOSDAQ'):
        df = fdr.DataReader(ticker, start=start.strftime('%Y-%m-%d'))
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns=str.title)
        df.index = pd.to_datetime(df.index)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    else:
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df.columns = df.columns.get_level_values(0)
        return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def parse_tradingview_csv(uploaded_file) -> pd.DataFrame:
    """TradingView 스크리너 CSV 파싱. 'Ticker' 컬럼 반환."""
    df = pd.read_csv(uploaded_file)
    # TradingView CSV 첫 컬럼이 티커
    ticker_col = df.columns[0]
    df = df.rename(columns={ticker_col: 'Ticker'})
    return df
```

- [ ] **Step 2: 수동 동작 확인**

```bash
python -c "
from data.fetcher import fetch_daily, fetch_index_daily
df = fetch_index_daily('SPY')
print(df.tail(3))
df_kr = fetch_index_daily('KOSPI')
print(df_kr.tail(3))
"
```
Expected: SPY와 KOSPI 최근 3일 OHLCV 출력

- [ ] **Step 3: 커밋**

```bash
git add data/fetcher.py
git commit -m "feat: data fetcher (yfinance + FinanceDataReader)"
```

---

## Task 4: DAY 페이즈 감지 (phases.py)

**Files:**
- Create: `strategy/phases.py`
- Create: `tests/test_phases.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_phases.py
import pandas as pd
import numpy as np
import pytest
from strategy.phases import detect_day1, detect_day2, PhaseResult

def make_df(closes, highs=None, lows=None, volumes=None):
    n = len(closes)
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame({
        'Open': closes,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
    })

def test_detect_day1_true():
    # 가격이 21EMA(~100) 아래로 떨어짐
    closes = [100.0] * 25 + [92.0]  # 마지막 봉이 EMA 아래
    df = make_df(closes)
    result = detect_day1(df)
    assert result is True

def test_detect_day1_false():
    closes = [100.0] * 26  # EMA 위에 있음
    df = make_df(closes)
    result = detect_day1(df)
    assert result is False

def test_detect_day2_true():
    # DAY1(하락) 이후 거래량 폭증 + 큰 양봉
    base_closes = [100.0] * 25
    day1_close = 92.0
    day2_close = 99.0
    closes = base_closes + [day1_close, day2_close]
    highs = [c * 1.01 for c in base_closes] + [93.0, 100.0]
    lows = [c * 0.99 for c in base_closes] + [90.0, 91.5]
    volumes = [1_000_000] * 25 + [1_000_000, 5_000_000]  # DAY2 거래량 5배
    df = make_df(closes, highs, lows, volumes)
    index_adr = 1.5  # 지수 ADR 1.5%
    result = detect_day2(df, index_adr=index_adr)
    assert result.is_day2 is True

def test_detect_day2_false_low_volume():
    base_closes = [100.0] * 25
    closes = base_closes + [92.0, 99.0]
    highs = [c * 1.01 for c in base_closes] + [93.0, 100.0]
    lows = [c * 0.99 for c in base_closes] + [90.0, 91.5]
    volumes = [1_000_000] * 27  # 거래량 증가 없음
    df = make_df(closes, highs, lows, volumes)
    result = detect_day2(df, index_adr=1.5)
    assert result.is_day2 is False
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_phases.py -v
```
Expected: ImportError

- [ ] **Step 3: phases.py 구현**

```python
# strategy/phases.py
from dataclasses import dataclass
import pandas as pd
import numpy as np
from strategy.indicators import calc_ema, calc_sma, calc_adr


@dataclass
class PhaseResult:
    is_day2: bool
    day1_candle_size: float   # abs(close - open) of DAY1
    day2_candle_size: float   # abs(close - open) of DAY2
    volume_ratio: float       # DAY2 volume / avg volume
    price_move_pct: float     # DAY2 price move %


def detect_day1(df: pd.DataFrame) -> bool:
    """최근 봉이 21EMA 아래로 내려갔는지 확인."""
    if len(df) < 22:
        return False
    ema21 = calc_ema(df, period=21)
    last_close = df['Close'].iloc[-1]
    last_ema = ema21.iloc[-1]
    return last_close < last_ema


def detect_day2(df: pd.DataFrame, index_adr: float) -> PhaseResult:
    """
    마지막 2개 봉 기준으로 DAY1→DAY2 패턴 감지.
    DAY2 조건:
      1. 이전 봉(DAY1)이 21EMA 아래
      2. 현재 봉(DAY2): 거래량 > 평균 거래량 × 1.5
      3. 현재 봉 상승폭 >= index_adr
      4. 현재 봉 양봉 크기 >= DAY1 봉 크기 × 0.8 (버금가는)
    """
    if len(df) < 25:
        return PhaseResult(False, 0, 0, 0, 0)

    ema21 = calc_ema(df, period=21)

    prev = df.iloc[-2]   # DAY1 후보
    curr = df.iloc[-1]   # DAY2 후보

    prev_ema = ema21.iloc[-2]
    day1_below_ema = prev['Close'] < prev_ema

    avg_vol = df['Volume'].iloc[-22:-2].mean()
    vol_ratio = curr['Volume'] / avg_vol if avg_vol > 0 else 0
    high_volume = vol_ratio >= 1.5

    price_move_pct = (curr['Close'] - curr['Open']) / curr['Open'] * 100
    big_move = price_move_pct >= index_adr

    day1_size = abs(prev['Close'] - prev['Open'])
    day2_size = abs(curr['Close'] - curr['Open'])
    candle_match = day2_size >= day1_size * 0.8 and curr['Close'] > curr['Open']

    is_day2 = day1_below_ema and high_volume and big_move and candle_match

    return PhaseResult(
        is_day2=is_day2,
        day1_candle_size=day1_size,
        day2_candle_size=day2_size,
        volume_ratio=vol_ratio,
        price_move_pct=price_move_pct,
    )


def get_phase_label(df: pd.DataFrame, index_adr: float) -> str:
    """현재 페이즈 레이블 반환: 'DAY1' | 'DAY2' | 'DAY3+' | 'Normal'"""
    if len(df) < 25:
        return 'Normal'
    if detect_day1(df):
        return 'DAY1'
    result = detect_day2(df, index_adr)
    if result.is_day2:
        return 'DAY2'
    # DAY3~5: DAY2 저점 유지 or 고점 돌파
    # 단순하게: 최근 3~5일 이내 DAY2가 있었는지 확인
    for lookback in range(2, 6):
        if len(df) < lookback + 25:
            break
        sub = df.iloc[:-lookback]
        sub_result = detect_day2(sub, index_adr)
        if sub_result.is_day2:
            day2_low = df.iloc[-(lookback + 1)]['Low']
            day2_high = df.iloc[-(lookback + 1)]['High']
            curr_close = df.iloc[-1]['Close']
            curr_low = df.iloc[-1]['Low']
            if curr_low >= day2_low or curr_close >= day2_high:
                return f'DAY{lookback + 1}'
    return 'Normal'
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_phases.py -v
```
Expected: 4개 PASSED

- [ ] **Step 5: 커밋**

```bash
git add strategy/phases.py tests/test_phases.py
git commit -m "feat: DAY1/DAY2 phase detection"
```

---

## Task 5: 스코어링 (scoring.py)

**Files:**
- Create: `strategy/scoring.py`
- Create: `tests/test_scoring.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_scoring.py
import pandas as pd
import numpy as np
from strategy.scoring import score_relative_strength, score_volume_asymmetry, score_candle_ratio, total_score

def make_df(closes, volumes=None, highs=None, lows=None):
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000] * n
    if highs is None:
        highs = [c * 1.01 for c in closes]
    if lows is None:
        lows = [c * 0.99 for c in closes]
    return pd.DataFrame({
        'Open': closes,
        'High': highs,
        'Low': lows,
        'Close': closes,
        'Volume': volumes
    })

def test_score_relative_strength_strong():
    # 종목은 오르는데 지수는 하락
    stock = make_df([100, 102, 104, 103, 105])
    index = make_df([100, 99, 98, 97, 96])
    score = score_relative_strength(stock, index)
    assert score == 1

def test_score_relative_strength_weak():
    # 종목도 같이 하락
    stock = make_df([100, 99, 98, 97, 96])
    index = make_df([100, 99, 98, 97, 96])
    score = score_relative_strength(stock, index)
    assert score == 0

def test_score_volume_asymmetry_good():
    # 상승일 거래량 많고 하락일 거래량 적음
    closes = [100, 98, 102, 99, 103]
    volumes = [500_000, 300_000, 2_000_000, 400_000, 1_800_000]
    df = make_df(closes, volumes)
    score = score_volume_asymmetry(df)
    assert score == 1

def test_score_candle_ratio_good():
    # 마지막 양봉이 이전 음봉보다 큼
    closes = [100, 95, 102]   # 음봉(-5), 양봉(+7)
    highs = [101, 100, 103]
    lows = [99, 94, 101]
    df = make_df(closes, highs=highs, lows=lows)
    score = score_candle_ratio(df)
    assert score == 1

def test_total_score_max():
    stock = make_df([100, 102, 104, 103, 105], volumes=[500_000, 2_000_000, 500_000, 2_000_000, 3_000_000])
    index = make_df([100, 99, 98, 97, 96])
    s = total_score(stock, index)
    assert 0 <= s <= 3
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
pytest tests/test_scoring.py -v
```
Expected: ImportError

- [ ] **Step 3: scoring.py 구현**

```python
# strategy/scoring.py
import pandas as pd
import numpy as np


def score_relative_strength(stock_df: pd.DataFrame, index_df: pd.DataFrame, lookback: int = 5) -> int:
    """
    지수 하락 중 종목이 보합/상승이면 1, 아니면 0.
    lookback 기간 동안 지수 수익률 < 0이고 종목 수익률 >= -0.5% 이면 강함.
    """
    if len(stock_df) < lookback or len(index_df) < lookback:
        return 0
    index_ret = (index_df['Close'].iloc[-1] - index_df['Close'].iloc[-lookback]) / index_df['Close'].iloc[-lookback] * 100
    stock_ret = (stock_df['Close'].iloc[-1] - stock_df['Close'].iloc[-lookback]) / stock_df['Close'].iloc[-lookback] * 100
    if index_ret < 0 and stock_ret >= -0.5:
        return 1
    if stock_ret > index_ret + 1.0:
        return 1
    return 0


def score_volume_asymmetry(df: pd.DataFrame, lookback: int = 10) -> int:
    """
    하락일 평균 거래량 < 상승일 평균 거래량이면 1, 아니면 0.
    """
    if len(df) < lookback:
        return 0
    recent = df.tail(lookback).copy()
    recent['is_up'] = recent['Close'] >= recent['Open']
    up_vol = recent[recent['is_up']]['Volume'].mean()
    down_vol = recent[~recent['is_up']]['Volume'].mean()
    if pd.isna(up_vol) or pd.isna(down_vol):
        return 0
    return 1 if up_vol > down_vol * 1.2 else 0


def score_candle_ratio(df: pd.DataFrame) -> int:
    """
    최근 양봉이 직전 음봉 크기를 잡거나 버금가면 1 (>= 80%), 아니면 0.
    """
    if len(df) < 3:
        return 0
    last = df.iloc[-1]
    prev = df.iloc[-2]
    last_body = abs(last['Close'] - last['Open'])
    prev_body = abs(prev['Close'] - prev['Open'])
    if last['Close'] <= last['Open']:  # 마지막 봉이 음봉이면 0
        return 0
    if prev_body == 0:
        return 1
    return 1 if last_body >= prev_body * 0.8 else 0


def total_score(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> int:
    """3가지 기준 합산 점수 (0~3)."""
    s1 = score_relative_strength(stock_df, index_df)
    s2 = score_volume_asymmetry(stock_df)
    s3 = score_candle_ratio(stock_df)
    return s1 + s2 + s3
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_scoring.py -v
```
Expected: 5개 PASSED

- [ ] **Step 5: 커밋**

```bash
git add strategy/scoring.py tests/test_scoring.py
git commit -m "feat: watchlist scoring (RS, volume asymmetry, candle ratio)"
```

---

## Task 6: 차트 컴포넌트 (charts.py)

**Files:**
- Create: `ui/charts.py`

- [ ] **Step 1: charts.py 작성**

```python
# ui/charts.py
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from strategy.indicators import calc_ema, calc_sma, calc_adr


def daily_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """일봉 캔들 차트 + EMA21 + SMA200 + 거래량."""
    if df.empty:
        return go.Figure()

    ema21 = calc_ema(df, 21)
    sma200 = calc_sma(df, 200)
    adr = calc_adr(df)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)

    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'], name=ticker,
        increasing_line_color='#ef5350', decreasing_line_color='#26a69a'
    ), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=ema21, name='EMA21',
                             line=dict(color='orange', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sma200, name='SMA200',
                             line=dict(color='purple', width=1.5)), row=1, col=1)

    colors = ['#ef5350' if c >= o else '#26a69a'
              for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='Volume',
                         marker_color=colors, showlegend=False), row=2, col=1)

    fig.update_layout(
        title=f'{ticker} — 일봉  |  ADR: {adr:.2f}%',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        height=600,
        margin=dict(l=40, r=40, t=60, b=20),
    )
    return fig


def intraday_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """5분봉 캔들 차트."""
    if df.empty:
        return go.Figure()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)

    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'], name=ticker,
        increasing_line_color='#ef5350', decreasing_line_color='#26a69a'
    ), row=1, col=1)

    colors = ['#ef5350' if c >= o else '#26a69a'
              for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='Volume',
                         marker_color=colors, showlegend=False), row=2, col=1)

    fig.update_layout(
        title=f'{ticker} — 5분봉',
        xaxis_rangeslider_visible=False,
        template='plotly_dark',
        height=500,
        margin=dict(l=40, r=40, t=60, b=20),
    )
    return fig
```

- [ ] **Step 2: 커밋**

```bash
git add ui/charts.py
git commit -m "feat: daily and intraday Plotly charts"
```

---

## Task 7: 지수 패널 + 와치리스트 UI

**Files:**
- Create: `ui/index_panel.py`
- Create: `ui/watchlist.py`

- [ ] **Step 1: index_panel.py 작성**

```python
# ui/index_panel.py
import streamlit as st
import pandas as pd
from data.fetcher import fetch_index_daily
from strategy.indicators import calc_adr
from strategy.phases import get_phase_label

INDEX_NAMES = ['KOSPI', 'KOSDAQ', 'SPY', 'QQQ']

PHASE_COLORS = {
    'DAY1': '🔴',
    'DAY2': '🟡',
    'DAY3': '🟢', 'DAY4': '🟢', 'DAY5': '🟢',
    'Normal': '⚪',
}


def render_index_panel():
    st.subheader('지수 현황')
    cols = st.columns(4)
    for col, name in zip(cols, INDEX_NAMES):
        with col:
            df = fetch_index_daily(name, days=300)
            if df.empty:
                st.metric(name, 'N/A')
                continue
            adr = calc_adr(df)
            phase = get_phase_label(df, adr)
            icon = PHASE_COLORS.get(phase, '⚪')
            last = df['Close'].iloc[-1]
            prev = df['Close'].iloc[-2]
            delta = (last - prev) / prev * 100
            st.metric(
                label=f'{icon} {name}',
                value=f'{last:,.2f}',
                delta=f'{delta:+.2f}%',
            )
            st.caption(f'ADR: {adr:.2f}%  |  Phase: {phase}')
```

- [ ] **Step 2: watchlist.py 작성**

```python
# ui/watchlist.py
import streamlit as st
import pandas as pd
from data.fetcher import fetch_daily, fetch_index_daily
from strategy.indicators import calc_adr, calc_rs
from strategy.phases import get_phase_label
from strategy.scoring import total_score


def build_watchlist(tickers: list[str], market: str) -> pd.DataFrame:
    """종목 리스트를 받아 RS, 스코어, 페이즈를 계산한 DataFrame 반환."""
    index_name = 'KOSPI' if market == 'KR_KOSPI' else ('KOSDAQ' if market == 'KR_KOSDAQ' else 'SPY')
    index_df = fetch_index_daily(index_name)
    index_adr = calc_adr(index_df) if not index_df.empty else 1.5

    rows = []
    for ticker in tickers:
        df = fetch_daily(ticker, market=market)
        if df.empty or len(df) < 25:
            continue
        rs = calc_rs(df, index_df)
        score = total_score(df, index_df)
        phase = get_phase_label(df, index_adr)
        last_close = df['Close'].iloc[-1]
        adr = calc_adr(df)
        rows.append({
            'Ticker': ticker,
            'Close': round(last_close, 2),
            'ADR%': round(adr, 2),
            'RS': round(rs, 3),
            'Score': score,
            'Phase': phase,
        })

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    result = result.sort_values(['Score', 'RS'], ascending=False).reset_index(drop=True)
    return result


def render_watchlist(kr_tickers: list[str], us_tickers: list[str]):
    st.subheader('와치리스트')
    tab_kospi, tab_kosdaq, tab_us = st.tabs(['KOSPI', 'KOSDAQ', 'US'])

    with tab_kospi:
        if not kr_tickers:
            st.info('CSV를 업로드하면 와치리스트가 생성됩니다.')
        else:
            with st.spinner('KOSPI 분석 중...'):
                df = build_watchlist(kr_tickers, market='KR_KOSPI')
            _render_table(df)

    with tab_kosdaq:
        st.info('KOSDAQ 종목은 CSV 업로드 후 자동 분류됩니다.')

    with tab_us:
        if not us_tickers:
            st.info('CSV를 업로드하면 와치리스트가 생성됩니다.')
        else:
            with st.spinner('US 분석 중...'):
                df = build_watchlist(us_tickers, market='US')
            _render_table(df)


def _render_table(df: pd.DataFrame):
    if df.empty:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    phase_icon = {'DAY1': '🔴', 'DAY2': '🟡', 'DAY3': '🟢',
                  'DAY4': '🟢', 'DAY5': '🟢', 'Normal': '⚪'}
    df['Phase'] = df['Phase'].map(lambda p: f"{phase_icon.get(p,'⚪')} {p}")
    df['Score'] = df['Score'].map(lambda s: '★' * s + '☆' * (3 - s))

    selected = st.dataframe(
        df, use_container_width=True,
        on_select='rerun', selection_mode='single-row',
        hide_index=True,
    )
    if selected and selected.selection.rows:
        row_idx = selected.selection.rows[0]
        st.session_state['selected_ticker'] = df.iloc[row_idx]['Ticker']
        st.session_state['selected_market'] = 'US'
```

- [ ] **Step 3: 커밋**

```bash
git add ui/index_panel.py ui/watchlist.py
git commit -m "feat: index panel and watchlist UI components"
```

---

## Task 8: 메인 앱 (app.py)

**Files:**
- Create: `app.py`

- [ ] **Step 1: app.py 작성**

```python
# app.py
import streamlit as st
import pandas as pd
from data.fetcher import parse_tradingview_csv
from ui.index_panel import render_index_panel
from ui.watchlist import render_watchlist, build_watchlist
from ui.charts import daily_chart, intraday_chart
from data.fetcher import fetch_daily, fetch_intraday

st.set_page_config(
    page_title='Stock Watchlist',
    page_icon='📈',
    layout='wide',
    initial_sidebar_state='expanded',
)

# ── 사이드바 ──────────────────────────────────────
with st.sidebar:
    st.title('📈 Stock Watchlist')

    kr_file = st.file_uploader('한국 스크리너 CSV (국장)', type='csv', key='kr_csv')
    us_file = st.file_uploader('미국 스크리너 CSV (미장)', type='csv', key='us_csv')

    if st.button('🔄 새로고침', use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption('TradingView 스크리너 → Export → CSV 업로드')

kr_tickers, us_tickers = [], []

if kr_file:
    try:
        kr_df = parse_tradingview_csv(kr_file)
        kr_tickers = kr_df['Ticker'].dropna().tolist()
        st.sidebar.success(f'한국 {len(kr_tickers)}개 종목 로드됨')
    except Exception as e:
        st.sidebar.error(f'CSV 파싱 오류: {e}')

if us_file:
    try:
        us_df = parse_tradingview_csv(us_file)
        us_tickers = us_df['Ticker'].dropna().tolist()
        st.sidebar.success(f'미국 {len(us_tickers)}개 종목 로드됨')
    except Exception as e:
        st.sidebar.error(f'CSV 파싱 오류: {e}')

# ── 메인 화면 ─────────────────────────────────────
render_index_panel()
st.divider()
render_watchlist(kr_tickers, us_tickers)

# ── 종목 상세 (클릭 시) ───────────────────────────
if 'selected_ticker' in st.session_state:
    ticker = st.session_state['selected_ticker']
    market = st.session_state.get('selected_market', 'US')
    st.divider()
    st.subheader(f'📊 {ticker} 상세')

    col1, col2 = st.columns(2)
    with col1:
        with st.spinner('일봉 로드 중...'):
            df_daily = fetch_daily(ticker, market=market)
        st.plotly_chart(daily_chart(df_daily, ticker), use_container_width=True)
    with col2:
        with st.spinner('5분봉 로드 중...'):
            df_5m = fetch_intraday(ticker, market=market)
        st.plotly_chart(intraday_chart(df_5m, ticker), use_container_width=True)
```

- [ ] **Step 2: 앱 실행 확인**

```bash
streamlit run app.py
```
Expected: 브라우저에서 `http://localhost:8501` 열림, 지수 현황 4개 카드 표시

- [ ] **Step 3: 커밋**

```bash
git add app.py
git commit -m "feat: main Streamlit app with sidebar and layout"
```

---

## Task 9: 전체 테스트 + 최종 확인

- [ ] **Step 1: 전체 테스트 실행**

```bash
pytest tests/ -v
```
Expected: 전체 PASSED (최소 14개 테스트)

- [ ] **Step 2: 앱 실행 후 수동 검증**

1. `streamlit run app.py` 실행
2. 지수 4개 (KOSPI, KOSDAQ, SPY, QQQ) 로드 확인
3. US 샘플 CSV 만들어서 업로드 테스트:
   ```
   Ticker
   NVDA
   TSLA
   AAPL
   ```
4. 와치리스트 테이블에 RS/Score/Phase 표시 확인
5. 종목 클릭 시 일봉 + 5분봉 차트 표시 확인

- [ ] **Step 3: 최종 커밋**

```bash
git add .
git commit -m "feat: stock watchlist dashboard complete"
```
