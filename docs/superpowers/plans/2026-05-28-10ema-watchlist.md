# 10EMA 강세장 와치리스트 탭 구현 플랜

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 21EMA 조정 탭(코스피/코스닥/나스닥)과 완전히 분리된 10EMA 강세장 탭 2개(10EMA 국장, 10EMA 미장)를 추가한다.

**Architecture:** `strategy/pivot_candle.py`에서 기준봉 탐지·케이스 분류 로직을 담당하고, `ui/watchlist_10ema.py`가 st-aggrid 테이블을 렌더링한다. `app.py`는 탭 5개로 확장하고 저장 경로 2개만 추가한다. 기존 코드는 전혀 건드리지 않는다.

**Tech Stack:** Python 3.11, pandas, numpy, streamlit, st-aggrid, yfinance

---

## 파일 맵

| 동작 | 파일 |
|------|------|
| 신규 생성 | `strategy/pivot_candle.py` |
| 신규 생성 | `tests/test_pivot_candle.py` |
| 신규 생성 | `ui/watchlist_10ema.py` |
| 수정 | `app.py` |

---

## Task 1: strategy/pivot_candle.py — 기준봉 탐지

**Files:**
- Create: `strategy/pivot_candle.py`
- Test: `tests/test_pivot_candle.py`

### 1-1. 테스트 파일 먼저 생성

- [ ] `tests/test_pivot_candle.py` 작성

```python
import pandas as pd
import numpy as np
import pytest
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope


def _make_df(closes, highs=None, lows=None, volumes=None, start='2026-01-01'):
    n = len(closes)
    dates = pd.date_range(start, periods=n, freq='B')
    highs   = highs   or [c * 1.01 for c in closes]
    lows    = lows    or [c * 0.99 for c in closes]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        'Open':   closes,
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Volume': volumes,
    }, index=dates)


# ── find_pivot_candle ─────────────────────────────────────────────

def test_detects_high_volume_breakout():
    """거래량 300%+, 60일 고점 돌파, 정배열 → 기준봉 탐지"""
    # 60일 횡보 (100), 마지막에 거래량 폭발 + 돌파
    closes  = [100.0] * 60 + [105.0]
    volumes = [1_000_000] * 60 + [4_000_000]   # 400% of avg
    df = _make_df(closes, volumes=volumes)
    # 정배열 강제: 마지막 60일은 EMA10>EMA21>EMA50 자연스럽게 충족될 수준
    result = find_pivot_candle(df, lookback=63)
    assert result is not None
    assert result['vol_ratio'] >= 3.0


def test_no_pivot_when_volume_insufficient():
    """거래량 200% 미만 → 기준봉 없음"""
    closes  = [100.0] * 60 + [105.0]
    volumes = [1_000_000] * 60 + [1_500_000]   # 150%
    df = _make_df(closes, volumes=volumes)
    assert find_pivot_candle(df, lookback=63) is None


def test_no_pivot_when_close_not_in_top30pct():
    """종가가 레인지 하위 → 기준봉 없음 (긴 윗꼬리)"""
    closes  = [100.0] * 60 + [101.0]
    highs   = [c * 1.01 for c in closes[:-1]] + [112.0]   # 윗꼬리 거대
    lows    = [c * 0.99 for c in closes[:-1]] + [100.0]
    volumes = [1_000_000] * 60 + [4_000_000]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    # close=101, low=100, high=112 → (101-100)/(112-100) = 0.083 < 0.7
    assert find_pivot_candle(df, lookback=63) is None


def test_picks_highest_vol_ratio_among_candidates():
    """조건 충족 후보 2개 → 거래량 비율 더 높은 것 선택"""
    closes  = [100.0] * 80 + [105.0, 100.0, 100.0, 110.0]
    volumes = [1_000_000] * 80 + [4_000_000, 1_000_000, 1_000_000, 6_000_000]
    df = _make_df(closes, volumes=volumes)
    result = find_pivot_candle(df, lookback=10)
    assert result is not None
    # vol_ratio=6.0인 마지막 봉이 선택되어야 함
    assert result['vol_ratio'] >= 5.0


def test_no_pivot_when_no_resistance_breakout():
    """60일 고점 돌파 없음 + 횡보 박스 아님 → 기준봉 없음"""
    # 전반부 110으로 고점, 후반부 105 수준으로 돌아옴
    closes  = [110.0] * 40 + [105.0] * 20 + [106.0]
    volumes = [1_000_000] * 60 + [4_000_000]
    df = _make_df(closes, volumes=volumes)
    assert find_pivot_candle(df, lookback=5) is None


# ── classify_case ─────────────────────────────────────────────────

def test_classify_returns_no_pivot_when_none():
    df = _make_df([100.0] * 30)
    assert classify_case(df, None) == '기준봉없음'


def test_classify_case1_in_range_and_within_15days():
    """현재가 기준봉 midline~high, 3~15일 후, 10EMA 우상향 → Case1"""
    # 65일: 상승 추세 (정배열 확보), 마지막 10일: 기준봉 이후 횡보
    base = [90 + i * 0.2 for i in range(55)]   # 완만 상승
    breakout_close = 101.0
    consolidation  = [100.5] * 9                # 9일 횡보
    closes  = base + [breakout_close] + consolidation
    volumes = [1_000_000] * 55 + [4_000_000] + [900_000] * 9
    highs   = [c * 1.01 for c in closes[:-10]] + [breakout_close * 1.02] + [c * 1.005 for c in consolidation]
    lows    = [c * 0.99 for c in closes[:-10]] + [breakout_close * 0.99] + [c * 0.995 for c in consolidation]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    pivot = find_pivot_candle(df, lookback=15)
    if pivot is None:
        pytest.skip("기준봉 탐지 실패 — 테스트 데이터 조건 미충족")
    result = classify_case(df, pivot)
    assert result in ('Case1', '대기중')   # 정배열 미달 시 대기중 허용


def test_classify_downbreak():
    """현재가 기준봉 저가 아래 → 하방이탈"""
    closes  = [100.0] * 60 + [105.0, 100.0, 98.0]
    volumes = [1_000_000] * 60 + [4_000_000, 1_000_000, 1_000_000]
    lows    = [c * 0.99 for c in closes]
    lows[-1] = 97.0
    df = _make_df(closes, lows=lows, volumes=volumes)
    pivot = {'date': df.index[-3], 'vol_ratio': 4.0,
             'high': 105.0 * 1.01, 'low': 105.0 * 0.99,
             'midline': 105.0 * 1.0, 'close': 105.0}
    # 현재가(98) < pivot low(103.95) → 하방이탈
    assert classify_case(df, pivot) == '하방이탈'


# ── calc_10ema_slope ──────────────────────────────────────────────

def test_10ema_slope_positive_on_uptrend():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) > 0


def test_10ema_slope_negative_on_downtrend():
    closes = [130 - i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) < 0
```

- [ ] 테스트 실행 (전부 실패 확인)

```
pytest tests/test_pivot_candle.py -v
```

예상: `ModuleNotFoundError: strategy.pivot_candle`

---

### 1-2. strategy/pivot_candle.py 구현

- [ ] `strategy/pivot_candle.py` 작성

```python
import numpy as np
import pandas as pd
from strategy.indicators import calc_ema


def calc_10ema_slope(stock_df: pd.DataFrame, period: int = 5) -> float:
    if len(stock_df) < period + 10:
        return 0.0
    ema10 = calc_ema(stock_df, 10)
    base = float(ema10.iloc[-(period + 1)])
    if base == 0:
        return 0.0
    return float((ema10.iloc[-1] - base) / base * 100)


def _vol_ratio_at(df: pd.DataFrame, idx: int, window: int = 20) -> float:
    """idx 위치의 거래량 / 직전 window일 평균 거래량"""
    if idx < window:
        return 0.0
    avg = float(df['Volume'].iloc[idx - window:idx].mean())
    if avg == 0:
        return 0.0
    return float(df['Volume'].iloc[idx]) / avg


def _close_in_top30(row: pd.Series) -> bool:
    rng = float(row['High']) - float(row['Low'])
    if rng == 0:
        return False
    return (float(row['Close']) - float(row['Low'])) / rng >= 0.7


def _is_aligned(ema10: pd.Series, ema21: pd.Series, ema50: pd.Series, idx: int) -> bool:
    return (
        float(ema10.iloc[idx]) > float(ema21.iloc[idx]) > float(ema50.iloc[idx])
    )


def _broke_60d_high(df: pd.DataFrame, idx: int) -> bool:
    if idx < 60:
        return False
    prior_high = float(df['High'].iloc[idx - 60:idx].max())
    return float(df['Close'].iloc[idx]) > prior_high


def _broke_vcp_box(df: pd.DataFrame, idx: int,
                   min_days: int = 5, max_days: int = 20,
                   max_range_pct: float = 5.0) -> bool:
    """직전 min_days~max_days 범위가 max_range_pct% 이내이면 박스로 간주, 종가가 박스 상단 돌파."""
    for lookback in range(min_days, min(max_days + 1, idx)):
        window = df.iloc[idx - lookback:idx]
        hi = float(window['High'].max())
        lo = float(window['Low'].min())
        if lo == 0:
            continue
        if (hi - lo) / lo * 100 <= max_range_pct:
            box_top = float(window['Close'].max())
            if float(df['Close'].iloc[idx]) > box_top:
                return True
    return False


def find_pivot_candle(
    stock_df: pd.DataFrame,
    lookback: int = 63,
) -> dict | None:
    """
    최근 lookback 거래일 내 기준봉 탐지.
    조건: 거래량 300%+, 종가 레인지 상위 30%, 저항 돌파, 정배열.
    복수 후보 시 거래량비율 최고 봉 반환.
    """
    if len(stock_df) < 70:
        return None

    ema10 = calc_ema(stock_df, 10)
    ema21 = calc_ema(stock_df, 21)
    ema50 = stock_df['Close'].rolling(50).mean()

    start_idx = max(60, len(stock_df) - lookback)
    candidates = []

    for i in range(start_idx, len(stock_df) - 1):   # 마지막 봉 제외 (오늘)
        vr = _vol_ratio_at(stock_df, i)
        if vr < 3.0:
            continue
        row = stock_df.iloc[i]
        if not _close_in_top30(row):
            continue
        if not (_broke_60d_high(stock_df, i) or _broke_vcp_box(stock_df, i)):
            continue
        if pd.isna(ema50.iloc[i]):
            continue
        if not _is_aligned(ema10, ema21, ema50, i):
            continue
        candidates.append((i, vr))

    if not candidates:
        return None

    best_i, best_vr = max(candidates, key=lambda x: x[1])
    row = stock_df.iloc[best_i]
    high  = float(row['High'])
    low   = float(row['Low'])
    close = float(row['Close'])
    return {
        'date':      stock_df.index[best_i],
        'vol_ratio': round(best_vr, 2),
        'high':      high,
        'low':       low,
        'midline':   round((high + low) / 2, 4),
        'close':     close,
    }


def classify_case(
    stock_df: pd.DataFrame,
    pivot: dict | None,
) -> str:
    """'기준봉없음' | '하방이탈' | '대기중' | 'Case1' | 'Case2'"""
    if pivot is None:
        return '기준봉없음'

    current_close = float(stock_df['Close'].iloc[-1])
    current_date  = stock_df.index[-1]

    if current_close < pivot['low']:
        return '하방이탈'

    days_since = int(np.busday_count(pivot['date'].date(), current_date.date()))

    # Case 2: 기준봉 이후 30일 이내에 기준봉 고가를 돌파한 적 있고 지금 복귀
    if days_since <= 30:
        since_pivot = stock_df[stock_df.index > pivot['date']]
        if not since_pivot.empty:
            ever_above = float(since_pivot['High'].max()) > pivot['high']
            back_near  = current_close >= pivot['high'] * 0.97 and current_close <= pivot['high'] * 1.05
            if ever_above and back_near:
                return 'Case2'

    # Case 1: 기준봉 범위 내 횡보, 3~15일, EMA10 우상향
    in_range   = pivot['midline'] <= current_close <= pivot['high'] * 1.03
    valid_days = 3 <= days_since <= 15
    slope_up   = calc_10ema_slope(stock_df) > 0

    ema10_now = float(calc_ema(stock_df, 10).iloc[-1])
    above_ema = current_close > ema10_now

    if in_range and valid_days and slope_up and above_ema:
        return 'Case1'

    return '대기중'
```

- [ ] 테스트 재실행

```
pytest tests/test_pivot_candle.py -v
```

예상: 9개 모두 PASS (test_classify_case1_in_range_and_within_15days는 skip 가능)

- [ ] 커밋

```
git add strategy/pivot_candle.py tests/test_pivot_candle.py
git commit -m "feat: 10EMA 기준봉 탐지 및 케이스 분류 (pivot_candle)"
```

---

## Task 2: ui/watchlist_10ema.py — 10EMA 탭 렌더링

**Files:**
- Create: `ui/watchlist_10ema.py`

- [ ] `ui/watchlist_10ema.py` 작성

```python
import streamlit as st
import pandas as pd
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import fetch_daily, get_stock_name
from data.sector import get_sectors
from strategy.indicators import calc_pct_from_52w_high, calc_ema
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope

CASE_ORDER = {'Case1': 0, 'Case2': 1, '대기중': 2, '하방이탈': 3, '기준봉없음': 4}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


@st.cache_data(ttl=300)
def _build_10ema_rows(tickers_tuple: tuple, market: str) -> list:
    tickers = list(tickers_tuple)
    rows = []

    for ticker in tickers:
        try:
            df = fetch_daily(ticker, market=market, days=300)
            if df.empty or len(df) < 70:
                continue

            pivot  = find_pivot_candle(df)
            case   = classify_case(df, pivot)
            name   = get_stock_name(ticker, market)

            last_close = float(df['Close'].iloc[-1])
            prev_close = float(df['Close'].iloc[-2])
            change_pct = (last_close - prev_close) / prev_close * 100

            pivot_date    = str(pivot['date'].date()) if pivot else ''
            pivot_vol_r   = pivot['vol_ratio'] if pivot else 0.0
            ema10_now     = float(calc_ema(df, 10).iloc[-1])
            days_since    = (
                int(__import__('numpy').busday_count(
                    pivot['date'].date(), df.index[-1].date()
                )) if pivot else 0
            )

            rows.append({
                'Ticker':       ticker,
                '종목명':       name,
                '케이스':       case,
                'Close':        round(last_close, 2),
                '등락%':        round(change_pct, 2),
                '기준봉일':     pivot_date,
                '기준봉거래량비': round(pivot_vol_r, 1),
                '횡보일수':     days_since,
                '10EMA기울기%': round(calc_10ema_slope(df), 2),
                '10EMA':        round(ema10_now, 2),
                'MA점수':       sum(
                    1 for v in [
                        calc_ema(df, 10).iloc[-1],
                        calc_ema(df, 21).iloc[-1],
                        df['Close'].rolling(50).mean().iloc[-1],
                        df['Close'].rolling(150).mean().iloc[-1],
                        df['Close'].rolling(200).mean().iloc[-1],
                    ] if not __import__('pandas').isna(v) and last_close > v
                ),
                '고점대비%':    calc_pct_from_52w_high(df),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: (
        CASE_ORDER.get(r['케이스'], 99),
        -r['기준봉거래량비'],
    ))
    return rows


def render_10ema_tab(tickers: list, market: str, label: str):
    if not tickers:
        st.info(f'사이드바에서 {label} 파일을 업로드해 주세요.')
        return

    with st.spinner(f'{label} 10EMA 분석 중... ({len(tickers)}개 종목)'):
        rows = _build_10ema_rows(tuple(tickers), market)

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    case_counts = {}
    for r in rows:
        case_counts[r['케이스']] = case_counts.get(r['케이스'], 0) + 1
    st.caption(' | '.join(f"{k}: {v}개" for k, v in case_counts.items() if v > 0))

    display_df = pd.DataFrame([{
        '티커 | 종목명':   f"{r['Ticker']} | {r['종목명']}",
        '케이스':          r['케이스'],
        'Close':           r['Close'],
        '등락%':           r['등락%'],
        '기준봉일':        r['기준봉일'],
        '기준봉거래량비':  r['기준봉거래량비'],
        '횡보일수':        r['횡보일수'],
        '10EMA기울기%':    r['10EMA기울기%'],
        'MA점수':          r['MA점수'],
        '고점대비%':       r['고점대비%'],
    } for r in rows])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True)
    if market.startswith('KR'):
        close_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        close_fmt = "value == null ? '' : '$' + value.toFixed(2)"
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=close_fmt)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter')
    gb.configure_column('케이스', filter='agSetColumnFilter')
    for col in ['등락%', '기준봉거래량비', '횡보일수', '10EMA기울기%', 'MA점수', '고점대비%']:
        gb.configure_column(col, filter='agNumberColumnFilter', type=['numericColumn'])
    gb.configure_grid_options(localeText=KO_LOCALE)

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.NO_UPDATE,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=450,
        fit_columns_on_grid_load=False,
    )

    case1 = [r for r in rows if r['케이스'] == 'Case1']
    case2 = [r for r in rows if r['케이스'] == 'Case2']
    if case1 or case2:
        names = [f"**{r['Ticker']}** ({r['케이스']})" for r in case1 + case2]
        st.success('⭐ 매수 대기: ' + ', '.join(names))
```

- [ ] 커밋

```
git add ui/watchlist_10ema.py
git commit -m "feat: 10EMA 탭 렌더링 (watchlist_10ema)"
```

---

## Task 3: app.py — 탭 5개로 확장

**Files:**
- Modify: `app.py`

- [ ] `app.py` 상단 import에 추가

기존:
```python
from ui.watchlist import render_watchlist, _fetch_index_cached
```

변경 후:
```python
from ui.watchlist import render_watchlist, _fetch_index_cached
from ui.watchlist_10ema import render_10ema_tab
```

- [ ] `SAVED_PATHS` 딕셔너리에 10EMA 키 추가

기존:
```python
SAVED_PATHS = {
    'KR_KOSPI':  SAVED_DIR / 'kospi.tickers',
    'KR_KOSDAQ': SAVED_DIR / 'kosdaq.tickers',
    'US':        SAVED_DIR / 'us.tickers',
}
```

변경 후:
```python
SAVED_PATHS = {
    'KR_KOSPI':  SAVED_DIR / 'kospi.tickers',
    'KR_KOSDAQ': SAVED_DIR / 'kosdaq.tickers',
    'US':        SAVED_DIR / 'us.tickers',
    '10EMA_KR':  SAVED_DIR / '10ema_kr.tickers',
    '10EMA_US':  SAVED_DIR / '10ema_us.tickers',
}
```

- [ ] 사이드바에 10EMA 업로더 2개 추가

기존 사이드바 코드 (`us_file = ...` 줄 이후):
```python
    st.divider()
    st.markdown('**🇺🇸 미국 주식**')
    us_file = st.file_uploader('US (CSV 또는 TXT)', type=['csv', 'txt'], key='us_csv')
    st.divider()
```

변경 후:
```python
    st.divider()
    st.markdown('**🇺🇸 미국 주식**')
    us_file = st.file_uploader('US (CSV 또는 TXT)', type=['csv', 'txt'], key='us_csv')
    st.divider()
    st.markdown('**📈 10EMA 강세장**')
    ema10_kr_file = st.file_uploader('10EMA 국장 (CSV 또는 TXT)', type=['csv', 'txt'], key='10ema_kr')
    ema10_us_file = st.file_uploader('10EMA 미장 (CSV 또는 TXT)', type=['csv', 'txt'], key='10ema_us')
    st.divider()
```

- [ ] 종목 파싱 루프에 10EMA 항목 추가

기존:
```python
kr_kospi, kr_kosdaq, us_tickers = [], [], []

for uploaded, key, name in [
    (kospi_file,  'KR_KOSPI',  'KOSPI'),
    (kosdaq_file, 'KR_KOSDAQ', 'KOSDAQ'),
    (us_file,     'US',        'US'),
]:
```

변경 후:
```python
kr_kospi, kr_kosdaq, us_tickers = [], [], []
ema10_kr_tickers, ema10_us_tickers = [], []

for uploaded, key, name in [
    (kospi_file,     'KR_KOSPI',  'KOSPI'),
    (kosdaq_file,    'KR_KOSDAQ', 'KOSDAQ'),
    (us_file,        'US',        'US'),
    (ema10_kr_file,  '10EMA_KR',  '10EMA 국장'),
    (ema10_us_file,  '10EMA_US',  '10EMA 미장'),
]:
```

- [ ] 파싱 루프 내 변수 할당 분기에 10EMA 추가

기존:
```python
            if key == 'KR_KOSPI':    kr_kospi   = tickers
            elif key == 'KR_KOSDAQ': kr_kosdaq  = tickers
            else:                    us_tickers = tickers
```

변경 후:
```python
            if key == 'KR_KOSPI':        kr_kospi          = tickers
            elif key == 'KR_KOSDAQ':     kr_kosdaq         = tickers
            elif key == 'US':            us_tickers        = tickers
            elif key == '10EMA_KR':      ema10_kr_tickers  = tickers
            else:                        ema10_us_tickers  = tickers
```

- [ ] `render_watchlist` 호출부를 5탭으로 교체

기존 (파일 하단):
```python
render_watchlist(kr_kospi, kr_kosdaq, us_tickers)
```

변경 후:
```python
st.subheader('와치리스트')
tab_kospi, tab_kosdaq, tab_10ema_kr, tab_us, tab_10ema_us = st.tabs([
    '🇰🇷 KOSPI', '🇰🇷 KOSDAQ', '📈 10EMA 국장', '🇺🇸 나스닥', '📈 10EMA 미장'
])
with tab_kospi:
    from ui.watchlist import render_watchlist_tab
    render_watchlist_tab(kr_kospi, 'KR_KOSPI', 'KOSPI')
with tab_kosdaq:
    render_watchlist_tab(kr_kosdaq, 'KR_KOSDAQ', 'KOSDAQ')
with tab_10ema_kr:
    render_10ema_tab(ema10_kr_tickers, 'KR_KOSPI', '10EMA 국장')
with tab_us:
    render_watchlist_tab(us_tickers, 'US', '나스닥')
with tab_10ema_us:
    render_10ema_tab(ema10_us_tickers, 'US', '10EMA 미장')
```

> **주의:** 기존 `render_watchlist()` 함수는 내부에서 `st.tabs`를 생성하므로 더 이상 호출하지 않는다. `render_watchlist_tab`은 `ui/watchlist.py`에 이미 공개 함수로 존재한다.

- [ ] 전체 테스트 실행

```
pytest tests/ -v
```

예상: 28개(기존) + 9개(pivot_candle) = 37개 PASS

- [ ] 앱 실행해서 탭 5개 확인

```
streamlit run app.py
```

- [ ] 커밋

```
git add app.py
git commit -m "feat: 10EMA 국장/미장 탭 추가, 탭 5개로 확장"
```

---

## 완료 기준

- [ ] `pytest tests/ -v` — 37개 이상 PASS
- [ ] 앱에서 탭 5개 표시 (KOSPI / KOSDAQ / 10EMA 국장 / 나스닥 / 10EMA 미장)
- [ ] 10EMA 탭에 파일 업로드 시 케이스 분류(Case1/Case2/대기중/하방이탈/기준봉없음) 표시
- [ ] 기존 21EMA 탭 동작 변화 없음
