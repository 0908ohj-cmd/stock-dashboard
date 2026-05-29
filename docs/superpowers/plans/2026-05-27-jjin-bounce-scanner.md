# 찐반등 스캐너 & RS 와치리스트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 지수 조정 구간을 자동 감지하고 찐반등 신호 출현 시 저점선행도·RS·MA점수 기준으로 재정렬된 와치리스트와 찐반등 날 5분봉 인트라데이 비교 차트를 제공한다.

**Architecture:** strategy 레이어에 market_status(찐반등 감지)·rs_correction(조정RS 계산) 모듈 신규 추가. ui 레이어의 watchlist를 전면 개편하고 intraday_overlay(5분봉 비교) 신규 추가. app.py는 시장 상태 카드와 오버레이를 연결한다.

**Tech Stack:** Python 3.11+, Streamlit, yfinance, pandas, numpy, plotly, streamlit-aggrid

---

## File Map

| 파일 | 상태 | 역할 |
|------|------|------|
| `strategy/market_status.py` | 신규 | 찐반등 감지, 시장 상태 관리 |
| `strategy/rs_correction.py` | 신규 | 조정 구간 RS·저점선행도 계산 |
| `ui/intraday_overlay.py` | 신규 | 5분봉 오버레이 차트·인트라데이 강도 지표 |
| `data/fetcher.py` | 수정 | fetch_intraday_for_date, fetch_index_intraday_for_date 추가 |
| `ui/watchlist.py` | 전면 개편 | 새 정렬 기준·컬럼으로 교체 |
| `app.py` | 수정 | 시장 상태 카드 + 오버레이 연결 |
| `tests/test_market_status.py` | 신규 | 찐반등 감지 단위 테스트 |
| `tests/test_rs_correction.py` | 신규 | RS·저점선행도 단위 테스트 |

---

## Task 1: strategy/market_status.py — TDD

**Files:**
- Create: `strategy/market_status.py`
- Create: `tests/test_market_status.py`

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_market_status.py
import pandas as pd
import numpy as np
import pytest
from strategy.market_status import detect_jjin_bounce, get_market_status


def _make_df(closes, opens=None, highs=None, lows=None, volumes=None):
    n = len(closes)
    dates = pd.date_range('2026-01-01', periods=n, freq='B')
    opens = opens or closes[:]
    highs = highs or [c * 1.01 for c in closes]
    lows  = lows  or [c * 0.99 for c in closes]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame(
        {'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes},
        index=dates,
    )


def _correction_df():
    """25일 상승 → 5일 하락(EMA21 이탈) → 1일 찐반등"""
    up   = [100 + i for i in range(25)]
    down = [124, 120, 116, 112, 108]
    # 반등: open=108, close=116  바디=8 > 이전음봉바디(4)*0.7  pct≈7.4%>ADR
    bounce_c, bounce_o = 116, 108
    closes  = up + down + [bounce_c]
    opens   = up + down + [bounce_o]
    highs   = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    return _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)


def test_detect_jjin_bounce_returns_none_on_plain_downtrend():
    closes = [100 - i for i in range(30)]
    df = _make_df(closes)
    assert detect_jjin_bounce(df) is None


def test_detect_jjin_bounce_detects_adr_covering_candle():
    df = _correction_df()
    result = detect_jjin_bounce(df)
    assert result is not None
    assert result['pct'] > 0
    assert result['cover_pct'] >= 70


def test_detect_jjin_bounce_gap_up_qualifies():
    """갭업(open=전일종가, close=+11%)도 조건 충족 시 인정"""
    up   = [100 + i for i in range(25)]
    down = [124, 120, 116, 112, 108]
    gap_c, gap_o = 120, 108   # +11%, 바디=12 > 이전음봉바디(4)*0.7
    closes  = up + down + [gap_c]
    opens   = up + down + [gap_o]
    highs   = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    df = _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    assert detect_jjin_bounce(df) is not None


def test_detect_jjin_bounce_fails_when_body_coverage_below_70pct():
    up   = [100 + i for i in range(25)]
    down = [124, 120, 116, 112, 108]
    # 바디=2 < 이전음봉바디(4)*0.7=2.8  → 커버 부족
    bounce_c, bounce_o = 110, 108
    closes  = up + down + [bounce_c]
    opens   = up + down + [bounce_o]
    highs   = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    df = _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    assert detect_jjin_bounce(df) is None


def test_get_market_status_normal():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    status = get_market_status(df)
    assert status['state'] == 'normal'


def test_get_market_status_correction():
    closes = [100 + i for i in range(25)] + [80] * 5
    df = _make_df(closes)
    status = get_market_status(df)
    assert status['state'] == 'correction'
    assert status['correction_start'] is not None


def test_get_market_status_early_signal():
    df = _correction_df()
    status = get_market_status(df)
    assert status['state'] in ('early_signal', 'ftd_confirmed')
    assert status['jjin_date'] is not None
    assert status['jjin_pct'] > 0
```

- [ ] **Step 2: 테스트 실행 → FAIL 확인**

```
cd C:\Users\PC\stock-dashboard
.\venv\Scripts\python.exe -m pytest tests/test_market_status.py -v
```
Expected: `ImportError` 또는 `ModuleNotFoundError`

- [ ] **Step 3: strategy/market_status.py 구현**

```python
# strategy/market_status.py
import pandas as pd
from strategy.indicators import calc_ema, calc_adr


def _ema21(index_df: pd.DataFrame) -> pd.Series:
    return calc_ema(index_df, 21)


def detect_jjin_bounce(index_df: pd.DataFrame) -> dict | None:
    """
    찐반등 감지 — 가장 최근 조건 충족일 반환.
    조건:
      1. 종가 < EMA21
      2. 당일 양봉, 상승폭 >= ADR(14일)
      3. 직전 봉 음봉 + 당일 바디 >= 직전 음봉 바디 * 0.7
    """
    if len(index_df) < 16:
        return None

    ema21    = _ema21(index_df)
    vol_ma20 = index_df['Volume'].rolling(20).mean()

    for i in range(len(index_df) - 1, 15, -1):
        row  = index_df.iloc[i]
        prev = index_df.iloc[i - 1]

        if float(row['Close']) >= float(ema21.iloc[i]):
            continue
        if float(row['Close']) < float(row['Open']):   # 양봉 아님
            continue
        if float(prev['Close']) >= float(prev['Open']): # 직전이 음봉 아님
            continue

        prev_close = float(index_df.iloc[i - 1]['Close'])
        pct_chg    = (float(row['Close']) - prev_close) / prev_close * 100
        adr_val    = float(
            ((index_df['High'] - index_df['Low']) / index_df['Close'] * 100)
            .iloc[max(0, i - 13):i + 1].mean()
        )
        if pct_chg < adr_val:
            continue

        curr_body = abs(float(row['Close']) - float(row['Open']))
        prev_body = abs(float(prev['Close']) - float(prev['Open']))
        if prev_body == 0 or curr_body < prev_body * 0.7:
            continue

        vol_ma    = float(vol_ma20.iloc[i]) if not pd.isna(vol_ma20.iloc[i]) else 0
        vol_ratio = float(row['Volume']) / vol_ma if vol_ma > 0 else 0
        stars     = 3 if vol_ratio >= 1.2 else (2 if vol_ratio >= 1.0 else 1)

        return {
            'date':      index_df.index[i],
            'pct':       round(pct_chg, 2),
            'adr':       round(adr_val, 2),
            'cover_pct': round(curr_body / prev_body * 100, 1),
            'vol_ratio': round(vol_ratio, 2),
            'stars':     stars,
        }

    return None


def _detect_ftd(index_df: pd.DataFrame, jjin_date: pd.Timestamp) -> pd.Timestamp | None:
    """FTD 확인 (보조): jjin_date 이후 Day4+, +1.7%, 전일 거래량 초과"""
    after = index_df[index_df.index > jjin_date]
    if len(after) < 4:
        return None

    jjin_low   = float(index_df.loc[jjin_date, 'Low'])
    after_list = list(after.iterrows())

    for i, (idx, row) in enumerate(after_list):
        if float(row['Low']) < jjin_low:
            return None
        if i < 3:
            continue
        prev_close = float(after_list[i - 1][1]['Close'])
        prev_vol   = float(after_list[i - 1][1]['Volume'])
        pct = (float(row['Close']) - prev_close) / prev_close * 100
        if pct >= 1.7 and float(row['Volume']) > prev_vol:
            return idx

    return None


def get_market_status(index_df: pd.DataFrame) -> dict:
    """
    지수 시장 상태 반환.
    state: 'normal' | 'correction' | 'early_signal' | 'ftd_confirmed'
    """
    base = {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None, 'jjin_pct': 0.0,
        'jjin_stars': 0,   'ftd_date': None,
    }
    if len(index_df) < 22:
        return base

    ema21        = _ema21(index_df)
    below        = index_df['Close'] < ema21
    transitions  = below.astype(int).diff()
    bd_starts    = transitions[transitions == 1].index
    rec_dates    = transitions[transitions == -1].index
    is_below_now = bool(below.iloc[-1])

    if len(bd_starts) == 0:
        return base

    last_start = bd_starts[-1]
    recs_after = rec_dates[rec_dates > last_start]

    if is_below_now:
        # 진행 중인 조정
        correction_slice = index_df[index_df.index >= last_start]
    else:
        # 가장 최근 완료된 조정
        last_end = recs_after[0] if len(recs_after) > 0 else index_df.index[-1]
        correction_slice = index_df[
            (index_df.index >= last_start) & (index_df.index <= last_end)
        ]

    base['correction_start'] = last_start

    if not is_below_now:
        base['state'] = 'normal'
        jjin = detect_jjin_bounce(correction_slice)
        if jjin:
            base['jjin_date']  = jjin['date']
            base['jjin_pct']   = jjin['pct']
            base['jjin_stars'] = jjin['stars']
        return base

    jjin = detect_jjin_bounce(correction_slice)
    if jjin is None:
        base['state'] = 'correction'
        return base

    ftd_date = _detect_ftd(index_df, jjin['date'])
    base.update({
        'state':       'ftd_confirmed' if ftd_date else 'early_signal',
        'jjin_date':   jjin['date'],
        'jjin_pct':    jjin['pct'],
        'jjin_stars':  jjin['stars'],
        'ftd_date':    ftd_date,
    })
    return base
```

- [ ] **Step 4: 테스트 실행 → PASS 확인**

```
.\venv\Scripts\python.exe -m pytest tests/test_market_status.py -v
```
Expected: 7 passed

- [ ] **Step 5: 커밋**

```
git add strategy/market_status.py tests/test_market_status.py
git commit -m "feat: add market_status — 찐반등 감지 및 시장 상태 관리"
```

---

## Task 2: strategy/rs_correction.py — TDD

**Files:**
- Create: `strategy/rs_correction.py`
- Create: `tests/test_rs_correction.py`

- [ ] **Step 1: 테스트 파일 작성**

```python
# tests/test_rs_correction.py
import pandas as pd
import pytest
from strategy.rs_correction import calc_correction_rs


def _make_df(closes, opens=None, volumes=None, start='2026-01-01'):
    n       = len(closes)
    dates   = pd.date_range(start, periods=n, freq='B')
    opens   = opens   or closes[:]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        'Open':   opens,
        'High':   [c * 1.01 for c in closes],
        'Low':    [c * 0.99 for c in closes],
        'Close':  closes,
        'Volume': volumes,
    }, index=dates)


def test_excess_pct_positive_when_stock_outperforms():
    dates      = pd.date_range('2026-01-01', periods=20, freq='B')
    idx_df     = _make_df([100 - i for i in range(20)])       # -19%
    stk_df     = _make_df([100 - i * 0.25 for i in range(20)]) # -4.75%
    result     = calc_correction_rs(
        stk_df, idx_df,
        correction_start=dates[0],
        jjin_date=dates[-1],
    )
    assert result['excess_pct'] > 0
    assert result['stock_pct'] > result['index_pct']


def test_lead_days_positive_when_stock_bottoms_first():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    # 지수: 20일 하락 후 10일 반등
    idx_closes = [100 - i * 2 for i in range(20)] + [60 + i * 2 for i in range(10)]
    # 종목: 10일 하락 후 20일 반등  (10일 먼저 저점)
    stk_closes = [100 - i * 4 for i in range(10)] + [60 + i * 2 for i in range(20)]
    result = calc_correction_rs(
        _make_df(stk_closes), _make_df(idx_closes),
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['lead_days'] > 0


def test_lead_days_negative_when_stock_bottoms_later():
    dates = pd.date_range('2026-01-01', periods=30, freq='B')
    idx_closes = [100 - i * 4 for i in range(10)] + [60 + i * 2 for i in range(20)]
    stk_closes = [100 - i * 2 for i in range(20)] + [60 + i * 2 for i in range(10)]
    result = calc_correction_rs(
        _make_df(stk_closes), _make_df(idx_closes),
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['lead_days'] < 0


def test_returns_zeros_on_insufficient_data():
    dates  = pd.date_range('2026-01-01', periods=3, freq='B')
    idx_df = _make_df([100, 98, 96])
    stk_df = _make_df([50, 49, 48])
    result = calc_correction_rs(
        stk_df, idx_df,
        correction_start=dates[0], jjin_date=dates[-1],
    )
    assert result['excess_pct'] == 0.0
    assert result['lead_days']  == 0
```

- [ ] **Step 2: 테스트 실행 → FAIL 확인**

```
.\venv\Scripts\python.exe -m pytest tests/test_rs_correction.py -v
```
Expected: `ImportError`

- [ ] **Step 3: strategy/rs_correction.py 구현**

```python
# strategy/rs_correction.py
import pandas as pd
from strategy.indicators import calc_ma_position


def _vol_ratio(df: pd.DataFrame) -> float:
    if len(df) < 4:
        return 0.0
    d = df.copy()
    d['is_up'] = d['Close'] >= d['Open']
    up   = d[d['is_up']]['Volume'].mean()
    down = d[~d['is_up']]['Volume'].mean()
    if pd.isna(up) or pd.isna(down) or down == 0:
        return 0.0
    return round(float(up / down), 2)


def _candle_ratio(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    bull = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open']))
        if float(r['Close']) > float(r['Open']) else 0, axis=1
    ).sum()
    bear = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open']))
        if float(r['Close']) < float(r['Open']) else 0, axis=1
    ).sum()
    if bear == 0:
        return 2.0 if bull > 0 else 1.0
    return round(float(bull / bear), 2)


def calc_correction_rs(
    stock_df: pd.DataFrame,
    index_df: pd.DataFrame,
    correction_start: pd.Timestamp,
    jjin_date: pd.Timestamp | None,
) -> dict:
    """
    조정 구간(correction_start ~ jjin_date) RS 계산.
    jjin_date가 None이면 index_df 마지막 날까지 계산.
    """
    empty = {
        'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0,
        'lead_days': 0, 'ma_score': 0,
        'vol_ratio': 0.0, 'candle_ratio': 0.0,
    }

    end = jjin_date if jjin_date is not None else index_df.index[-1]

    idx_slice = index_df[
        (index_df.index >= correction_start) & (index_df.index <= end)
    ]
    stk_slice = stock_df[
        (stock_df.index >= correction_start) & (stock_df.index <= end)
    ]

    if len(idx_slice) < 4 or len(stk_slice) < 4:
        return empty

    idx_pct = (float(idx_slice['Close'].iloc[-1]) / float(idx_slice['Close'].iloc[0]) - 1) * 100
    stk_pct = (float(stk_slice['Close'].iloc[-1]) / float(stk_slice['Close'].iloc[0]) - 1) * 100

    idx_bottom = idx_slice['Close'].idxmin()
    stk_bottom = stk_slice['Close'].idxmin()
    lead_days  = int((idx_bottom - stk_bottom).days)

    stk_for_ma = stock_df[stock_df.index <= end]
    ma_score   = calc_ma_position(stk_for_ma) if len(stk_for_ma) >= 10 else 0

    return {
        'stock_pct':    round(stk_pct, 2),
        'index_pct':    round(idx_pct, 2),
        'excess_pct':   round(stk_pct - idx_pct, 2),
        'lead_days':    lead_days,
        'ma_score':     ma_score,
        'vol_ratio':    _vol_ratio(stk_slice),
        'candle_ratio': _candle_ratio(stk_slice),
    }
```

- [ ] **Step 4: 테스트 실행 → PASS 확인**

```
.\venv\Scripts\python.exe -m pytest tests/test_rs_correction.py -v
```
Expected: 4 passed

- [ ] **Step 5: 전체 테스트 이상 없음 확인**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```
Expected: 19 passed

- [ ] **Step 6: 커밋**

```
git add strategy/rs_correction.py tests/test_rs_correction.py
git commit -m "feat: add rs_correction — 조정 구간 RS·저점선행도 계산"
```

---

## Task 3: data/fetcher.py — 특정 날짜 5분봉 추가

**Files:**
- Modify: `data/fetcher.py`

- [ ] **Step 1: fetcher.py에 두 함수 추가**

파일 하단 `parse_tradingview_csv` 아래에 추가:

```python
def fetch_intraday_for_date(
    ticker: str, target_date, market: str = 'US'
) -> pd.DataFrame:
    """특정 날짜 5분봉. 60일 초과 시 빈 DataFrame 반환."""
    from datetime import datetime as dt
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.to_pydatetime()
    if (dt.today() - target_date).days > 59:
        return pd.DataFrame()

    if market.startswith('KR'):
        suffix   = '.KS' if 'KOSPI' in market else '.KQ'
        yf_ticker = ticker + suffix
    else:
        yf_ticker = ticker

    start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(days=1)
    df = yf.download(yf_ticker, start=start, end=end, interval='5m',
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
    return df[needed].dropna()


def fetch_index_intraday_for_date(name: str, target_date) -> pd.DataFrame:
    """지수 특정 날짜 5분봉."""
    from datetime import datetime as dt
    if isinstance(target_date, pd.Timestamp):
        target_date = target_date.to_pydatetime()
    if (dt.today() - target_date).days > 59:
        return pd.DataFrame()

    ticker = INDICES[name]
    start  = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end    = start + timedelta(days=1)
    df = yf.download(ticker, start=start, end=end, interval='5m',
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = [c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]
    return df[needed].dropna()
```

- [ ] **Step 2: 동작 확인 (NASDAQ, 최근 날짜)**

```
.\venv\Scripts\python.exe -c "
from data.fetcher import fetch_index_intraday_for_date
import pandas as pd
df = fetch_index_intraday_for_date('QQQ', pd.Timestamp('2026-05-23'))
print(df.head(3))
print('rows:', len(df))
"
```
Expected: 5분봉 데이터 출력 (장 시간 기준 약 78행)

- [ ] **Step 3: 커밋**

```
git add data/fetcher.py
git commit -m "feat: add fetch_intraday_for_date for specific-date 5m data"
```

---

## Task 4: ui/intraday_overlay.py — 5분봉 오버레이

**Files:**
- Create: `ui/intraday_overlay.py`

- [ ] **Step 1: ui/intraday_overlay.py 작성**

```python
# ui/intraday_overlay.py
import pandas as pd
import plotly.graph_objects as go


def calc_intraday_strength(
    stock_5m: pd.DataFrame,
    index_5m: pd.DataFrame,
) -> dict:
    """찐반등 날 5분봉 인트라데이 강도 지표."""
    if stock_5m.empty or index_5m.empty:
        return {}

    idx_open = float(index_5m['Close'].iloc[0])
    stk_open = float(stock_5m['Close'].iloc[0])

    # 지수 당일 고점 시각
    idx_peak_time = index_5m['High'].idxmax()

    # 지수 고점 이후 구간
    idx_after = index_5m[index_5m.index >= idx_peak_time]
    stk_after = stock_5m[stock_5m.index >= idx_peak_time]

    if len(idx_after) >= 2 and len(stk_after) >= 2:
        idx_peak_ret = (float(idx_after['Close'].iloc[-1]) / float(idx_after['Close'].iloc[0]) - 1) * 100
        stk_peak_ret = (float(stk_after['Close'].iloc[-1]) / float(stk_after['Close'].iloc[0]) - 1) * 100
        excess_after_peak = round(stk_peak_ret - idx_peak_ret, 2)
    else:
        excess_after_peak = 0.0

    def count_low_breaks(df: pd.DataFrame) -> int:
        return sum(
            1 for i in range(1, len(df))
            if float(df['Low'].iloc[i]) < float(df['Low'].iloc[i - 1])
        )

    def count_high_updates(df: pd.DataFrame) -> int:
        updates, running = 0, float(df['High'].iloc[0])
        for i in range(1, len(df)):
            h = float(df['High'].iloc[i])
            if h > running:
                updates += 1
                running = h
        return updates

    idx_day_high = float(index_5m['High'].max())
    stk_day_high = float(stock_5m['High'].max())
    idx_close    = float(index_5m['Close'].iloc[-1])
    stk_close    = float(stock_5m['Close'].iloc[-1])

    return {
        'index_peak_time':      idx_peak_time,
        'excess_after_peak_pct': excess_after_peak,
        'stock_low_breaks':     count_low_breaks(stock_5m),
        'index_low_breaks':     count_low_breaks(index_5m),
        'stock_high_updates':   count_high_updates(stock_5m),
        'index_high_updates':   count_high_updates(index_5m),
        'stock_close_ratio':    round(stk_close / stk_day_high * 100, 1) if stk_day_high else 0,
        'index_close_ratio':    round(idx_close / idx_day_high * 100, 1) if idx_day_high else 0,
    }


def intraday_overlay_chart(
    stock_5m: pd.DataFrame,
    index_5m: pd.DataFrame,
    ticker: str,
    index_name: str,
) -> go.Figure:
    """당일 시가=0% 정규화 누적수익률 오버레이 차트."""
    fig = go.Figure()

    if stock_5m.empty or index_5m.empty:
        fig.update_layout(title='5분봉 데이터 없음 (60일 초과)', template='plotly_dark', height=350)
        return fig

    idx_open   = float(index_5m['Close'].iloc[0])
    stk_open   = float(stock_5m['Close'].iloc[0])
    idx_cumret = (index_5m['Close'] / idx_open - 1) * 100
    stk_cumret = (stock_5m['Close'] / stk_open - 1) * 100
    idx_peak_time = index_5m['High'].idxmax()

    fig.add_trace(go.Scatter(
        x=index_5m.index, y=idx_cumret,
        name=index_name,
        line=dict(color='#888888', width=1.5, dash='dot'),
    ))
    fig.add_trace(go.Scatter(
        x=stock_5m.index, y=stk_cumret,
        name=ticker,
        line=dict(color='#ef5350', width=2),
    ))
    fig.add_vline(
        x=idx_peak_time.timestamp() * 1000,
        line_width=1, line_dash='dash', line_color='#ffb74d',
        annotation_text='지수 고점',
        annotation_position='top right',
    )
    fig.add_hline(y=0, line_width=0.5, line_color='#444')

    fig.update_layout(
        title=f'{ticker} vs {index_name} — 찐반등 날 5분봉',
        yaxis_title='시가 대비 수익률 (%)',
        template='plotly_dark',
        height=380,
        margin=dict(l=40, r=40, t=50, b=20),
        legend=dict(orientation='h', y=1.02),
    )
    return fig
```

- [ ] **Step 2: import 확인**

```
.\venv\Scripts\python.exe -c "from ui.intraday_overlay import calc_intraday_strength, intraday_overlay_chart; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: 커밋**

```
git add ui/intraday_overlay.py
git commit -m "feat: add intraday_overlay — 5분봉 오버레이 차트 및 인트라데이 강도 지표"
```

---

## Task 5: ui/watchlist.py — 전면 개편

**Files:**
- Modify: `ui/watchlist.py` (전면 교체)

- [ ] **Step 1: watchlist.py 전체 교체**

```python
# ui/watchlist.py
import streamlit as st
import pandas as pd
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import fetch_daily, fetch_index_daily, get_stock_name
from data.sector import get_sectors
from strategy.market_status import get_market_status
from strategy.rs_correction import calc_correction_rs

INDEX_FOR_MARKET = {
    'KR_KOSPI':  'KOSPI',
    'KR_KOSDAQ': 'KOSDAQ',
    'US':        'QQQ',
}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


@st.cache_data(ttl=300)
def _fetch_index_cached(name: str) -> pd.DataFrame:
    return fetch_index_daily(name)


@st.cache_data(ttl=300)
def _get_market_status_cached(market: str) -> dict:
    index_name = INDEX_FOR_MARKET.get(market, 'QQQ')
    index_df   = _fetch_index_cached(index_name)
    return get_market_status(index_df) if not index_df.empty else {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None,  'jjin_pct': 0.0,
        'jjin_stars': 0,    'ftd_date': None,
    }


@st.cache_data(ttl=300)
def _build_rows(
    tickers_tuple: tuple,
    market: str,
    correction_start_str: str | None,
    jjin_date_str: str | None,
) -> list:
    tickers    = list(tickers_tuple)
    index_name = INDEX_FOR_MARKET.get(market, 'QQQ')
    index_df   = _fetch_index_cached(index_name)

    correction_start = pd.Timestamp(correction_start_str) if correction_start_str else None
    jjin_date        = pd.Timestamp(jjin_date_str)        if jjin_date_str        else None

    stock_cache = {}
    for ticker in tickers:
        try:
            df = fetch_daily(ticker, market=market)
            if not df.empty and len(df) >= 25:
                stock_cache[ticker] = df
        except Exception:
            continue

    sectors = get_sectors(list(stock_cache.keys()), market)
    rows    = []

    for ticker, df in stock_cache.items():
        try:
            last_close  = float(df['Close'].iloc[-1])
            prev_close  = float(df['Close'].iloc[-2])
            change_pct  = (last_close - prev_close) / prev_close * 100
            name        = get_stock_name(ticker, market)

            if correction_start is not None and not index_df.empty:
                rs = calc_correction_rs(df, index_df, correction_start, jjin_date)
            else:
                rs = {
                    'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0,
                    'lead_days': 0,   'ma_score': 0,
                    'vol_ratio': 0.0, 'candle_ratio': 0.0,
                }

            rows.append({
                'Ticker':    ticker,
                '종목명':    name,
                '섹터':      sectors.get(ticker, '기타'),
                'Close':     round(last_close, 2),
                '등락%':     round(change_pct, 2),
                '저점선행':  rs['lead_days'],
                '조정RS%':   rs['excess_pct'],
                'MA점수':    rs['ma_score'],
                '거래량비%': round(rs['vol_ratio'] * 100, 0),
                '양봉비%':   round(rs['candle_ratio'] * 100, 0),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: (
        -r['저점선행'],
        -(r['조정RS%'] or 0),
        -r['MA점수'],
        -(r['거래량비%'] or 0),
    ))
    return rows


def _status_banner(status: dict, label: str):
    state = status['state']
    if state == 'early_signal':
        stars = '★' * status['jjin_stars']
        jdate = status['jjin_date'].date() if status['jjin_date'] else ''
        st.success(f"⚡ **{label} 찐반등 감지!** {jdate}  +{status['jjin_pct']}%  {stars}")
    elif state == 'ftd_confirmed':
        stars = '★' * status['jjin_stars']
        jdate = status['jjin_date'].date() if status['jjin_date'] else ''
        fdate = status['ftd_date'].date()  if status['ftd_date']  else ''
        st.success(f"✅ **{label} FTD 확인!** 찐반등 {jdate} → FTD {fdate}  {stars}")
    elif state == 'correction':
        cdate = status['correction_start'].date() if status['correction_start'] else ''
        st.warning(f"🔴 **{label} 조정 중** (이탈일: {cdate})")
    else:
        st.info(f"✅ **{label} 정상** (21EMA 위)")


def render_watchlist_tab(tickers: list, market: str, label: str):
    if not tickers:
        st.info(f'사이드바에서 {label} CSV를 업로드해 주세요.')
        return

    status = _get_market_status_cached(market)
    _status_banner(status, label)

    cs = status['correction_start']
    jd = status['jjin_date']
    correction_start_str = str(cs.date()) if cs else None
    jjin_date_str        = str(jd.date()) if jd else None

    with st.spinner(f'{label} 분석 중... ({len(tickers)}개 종목)'):
        rows = _build_rows(
            tuple(tickers), market,
            correction_start_str, jjin_date_str,
        )

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    if correction_start_str:
        end_str = jjin_date_str or '진행 중'
        st.caption(f"📅 조정 구간: {correction_start_str} ~ {end_str}")

    display_df = pd.DataFrame([{
        '티커 | 종목명': f"{r['Ticker']} | {r['종목명']}",
        '섹터':          r['섹터'],
        'Close':         r['Close'],
        '등락%':         r['등락%'],
        '저점선행(일)':  r['저점선행'],
        '조정RS%':       r['조정RS%'],
        'MA점수':        r['MA점수'],
        '거래량비%':     r['거래량비%'],
        '양봉비%':       r['양봉비%'],
    } for r in rows])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True)
    if market.startswith('KR'):
        close_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        close_fmt = "value == null ? '' : '$' + value.toFixed(2)"
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=close_fmt)
    for col in ['등락%', '저점선행(일)', '조정RS%', 'MA점수', '거래량비%', '양봉비%']:
        gb.configure_column(col, filter='agNumberColumnFilter', type=['numericColumn'])
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter')
    gb.configure_column('섹터', filter='agSetColumnFilter')
    gb.configure_selection('single', use_checkbox=False)
    gb.configure_grid_options(localeText=KO_LOCALE)

    grid_response = AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=420,
        fit_columns_on_grid_load=False,
    )

    selected_rows = grid_response.get('selected_rows')
    if selected_rows is not None and len(selected_rows) > 0:
        selected_ticker = selected_rows[0]['티커 | 종목명'].split(' | ')[0]
        selected_row    = next((r for r in rows if r['Ticker'] == selected_ticker), None)
        st.session_state['selected_ticker'] = selected_ticker
        st.session_state['selected_market'] = market
        if selected_row:
            st.session_state['selected_jjin_date'] = jjin_date_str


def render_watchlist(kr_kospi: list, kr_kosdaq: list, us_tickers: list):
    st.subheader('와치리스트')
    with st.expander('컬럼 설명', expanded=False):
        st.markdown('- **저점선행(일)**: 지수 저점보다 N일 먼저 저점 찍은 종목. 양수일수록 우선')
        st.markdown('- **조정RS%**: 조정 구간(21EMA 이탈~찐반등) 동안 지수 대비 초과수익률')
        st.markdown('- **MA점수**: EMA10/21, SMA50/150/200 위에 있는 개수 (0~5)')
        st.markdown('- **거래량비%**: 상승일/하락일 평균거래량 비율 ×100. 120 초과 = 좋음')
        st.markdown('- **양봉비%**: 누적 양봉바디/음봉바디 비율 ×100. 100 초과 = 양봉 우세')

    tab_kospi, tab_kosdaq, tab_us = st.tabs(['🇰🇷 KOSPI', '🇰🇷 KOSDAQ', '🇺🇸 US'])
    with tab_kospi:
        render_watchlist_tab(kr_kospi,  'KR_KOSPI',  'KOSPI')
    with tab_kosdaq:
        render_watchlist_tab(kr_kosdaq, 'KR_KOSDAQ', 'KOSDAQ')
    with tab_us:
        render_watchlist_tab(us_tickers, 'US', 'US')
```

- [ ] **Step 2: import 확인**

```
.\venv\Scripts\python.exe -c "from ui.watchlist import render_watchlist; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: 커밋**

```
git add ui/watchlist.py
git commit -m "feat: rewrite watchlist — 저점선행도·RS 기반 정렬 + 시장 상태 배너"
```

---

## Task 6: app.py — 시장 상태 카드 + 5분봉 오버레이 연결

**Files:**
- Modify: `app.py`

- [ ] **Step 1: app.py 전체 교체**

```python
# app.py
import streamlit as st
from data.fetcher import (
    parse_tradingview_csv, fetch_daily, fetch_intraday,
    fetch_index_daily, fetch_intraday_for_date, fetch_index_intraday_for_date,
)
from ui.index_panel import render_index_panel
from ui.watchlist import render_watchlist, _get_market_status_cached, _fetch_index_cached
from ui.charts import daily_chart
from ui.intraday_overlay import calc_intraday_strength, intraday_overlay_chart
from strategy.market_status import get_market_status
import pandas as pd

st.set_page_config(
    page_title='Stock Watchlist',
    page_icon='📈',
    layout='wide',
    initial_sidebar_state='expanded',
)

INDEX_FOR_MARKET = {
    'KR_KOSPI':  'KOSPI',
    'KR_KOSDAQ': 'KOSDAQ',
    'US':        'QQQ',
}

# ── 사이드바 ──────────────────────────────────────────────
with st.sidebar:
    st.title('📈 Stock Watchlist')
    st.caption('TradingView 스크리너 → Export → CSV 업로드')
    st.divider()

    st.markdown('**🇰🇷 한국 주식**')
    kospi_file  = st.file_uploader('KOSPI 스크리너 CSV', type='csv', key='kospi_csv')
    kosdaq_file = st.file_uploader('KOSDAQ 스크리너 CSV', type='csv', key='kosdaq_csv')
    st.divider()
    st.markdown('**🇺🇸 미국 주식**')
    us_file = st.file_uploader('US 스크리너 CSV', type='csv', key='us_csv')
    st.divider()
    if st.button('🔄 새로고침', use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption('⚠️ 주가 데이터는 15분 지연 (무료 API)')

# ── 종목 파싱 ─────────────────────────────────────────────
kr_kospi, kr_kosdaq, us_tickers = [], [], []

for uploaded, key, name in [
    (kospi_file,  'KR_KOSPI',  'KOSPI'),
    (kosdaq_file, 'KR_KOSDAQ', 'KOSDAQ'),
    (us_file,     'US',        'US'),
]:
    if uploaded:
        try:
            df = parse_tradingview_csv(uploaded)
            tickers = df['Ticker'].dropna().astype(str).tolist()
            if key == 'KR_KOSPI':   kr_kospi   = tickers
            elif key == 'KR_KOSDAQ': kr_kosdaq  = tickers
            else:                    us_tickers = tickers
            st.sidebar.success(f'{name} {len(tickers)}개 로드됨')
        except Exception as e:
            st.sidebar.error(f'CSV 오류: {e}')

# ── 지수 패널 ─────────────────────────────────────────────
render_index_panel()
st.divider()

# ── 와치리스트 ────────────────────────────────────────────
render_watchlist(kr_kospi, kr_kosdaq, us_tickers)

# ── 종목 상세 ─────────────────────────────────────────────
if st.session_state.get('selected_ticker'):
    ticker   = st.session_state['selected_ticker']
    market   = st.session_state.get('selected_market', 'US')
    jjin_str = st.session_state.get('selected_jjin_date')

    index_name = INDEX_FOR_MARKET.get(market, 'QQQ')
    st.divider()
    st.subheader(f'📊 {ticker} 상세')

    col1, col2 = st.columns(2)

    with col1:
        with st.spinner('일봉 로드 중...'):
            df_daily = fetch_daily(ticker, market=market)
            idx_df   = _fetch_index_cached(index_name)
        if not df_daily.empty:
            st.plotly_chart(daily_chart(df_daily, ticker, index_df=idx_df), use_container_width=True)

    with col2:
        if jjin_str:
            jjin_date = pd.Timestamp(jjin_str)
            st.markdown(f'**찐반등 날 5분봉 비교** ({jjin_date.date()})')
            with st.spinner('5분봉 로드 중...'):
                stock_5m = fetch_intraday_for_date(ticker, jjin_date, market=market)
                index_5m = fetch_index_intraday_for_date(index_name, jjin_date)

            if not stock_5m.empty and not index_5m.empty:
                strength = calc_intraday_strength(stock_5m, index_5m)
                fig = intraday_overlay_chart(stock_5m, index_5m, ticker, index_name)
                st.plotly_chart(fig, use_container_width=True)

                m1, m2, m3, m4 = st.columns(4)
                m1.metric('지수고점 후 초과상승', f"{strength.get('excess_after_peak_pct', 0):+.2f}%")
                m2.metric(
                    '고점갱신',
                    f"종목 {strength.get('stock_high_updates', 0)}회",
                    f"지수 {strength.get('index_high_updates', 0)}회",
                )
                m3.metric(
                    '저점이탈',
                    f"종목 {strength.get('stock_low_breaks', 0)}회",
                    f"지수 {strength.get('index_low_breaks', 0)}회",
                    delta_color='inverse',
                )
                m4.metric(
                    '종가/고점',
                    f"종목 {strength.get('stock_close_ratio', 0):.1f}%",
                    f"지수 {strength.get('index_close_ratio', 0):.1f}%",
                )
            else:
                st.info('5분봉 데이터 없음 (찐반등일이 60일 초과)')
        else:
            with st.spinner('5분봉 로드 중...'):
                df_5m = fetch_intraday(ticker, market=market)
            if not df_5m.empty:
                from ui.charts import intraday_chart
                st.plotly_chart(intraday_chart(df_5m, ticker), use_container_width=True)
```

- [ ] **Step 2: 전체 테스트 이상 없음 확인**

```
.\venv\Scripts\python.exe -m pytest tests/ -v
```
Expected: 19 passed

- [ ] **Step 3: Streamlit 앱 실행 확인**

```
.\venv\Scripts\python.exe -m streamlit run app.py
```
브라우저에서 확인:
- [ ] 시장 상태 배너 표시 (KOSPI/KOSDAQ/US 탭 각각)
- [ ] 와치리스트 컬럼: 저점선행(일), 조정RS%, MA점수 표시
- [ ] 종목 클릭 → 일봉 + 5분봉 오버레이 표시 (찐반등 60일 이내인 경우)

- [ ] **Step 4: 임시 분석 파일 정리**

```
del C:\Users\PC\stock-dashboard\analyze_ftd.py
del C:\Users\PC\stock-dashboard\analyze_ftd2.py
```

- [ ] **Step 5: 최종 커밋**

```
git add app.py
git commit -m "feat: integrate 찐반등 스캐너 — 시장 상태 카드 + 5분봉 오버레이"
```
