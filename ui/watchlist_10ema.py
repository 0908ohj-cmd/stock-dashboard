import streamlit as st
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import fetch_daily, get_stock_name
from data.sector import get_sectors
from strategy.indicators import calc_pct_from_52w_high, calc_ema
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope

# 정렬 우선순위: 셋업 → 형성중 → 이탈류 → 없음
STATE_ORDER = {'셋업': 0, '형성중': 1, '돌파완료': 2, '10EMA이탈': 3, '중간선이탈': 4, '이탈': 5, '없음': 6}

STATE_BADGE = {
    '셋업':     '🟢 셋업',
    '형성중':   '🟡 형성중',
    '돌파완료': '🔵 돌파완료',
    '10EMA이탈':'🔴 10EMA이탈',
    '중간선이탈':'🔴 중간선이탈',
    '이탈':     '🔴 이탈',
    '없음':     '🔴 없음',
}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


def _ma_score(df: pd.DataFrame, close: float) -> int:
    mas = [
        calc_ema(df, 10).iloc[-1],
        calc_ema(df, 21).iloc[-1],
        df['Close'].rolling(50).mean().iloc[-1],
        df['Close'].rolling(150).mean().iloc[-1],
        df['Close'].rolling(200).mean().iloc[-1],
    ]
    return sum(1 for v in mas if not pd.isna(v) and close > v)


def _process_one(ticker: str, market: str) -> dict | None:
    try:
        df = fetch_daily(ticker, market=market, days=300)
        if df.empty or len(df) < 70:
            return None

        # ADR 6% 미만 제외 (변동성 부족 종목)
        adr = float(((df['High'] - df['Low']) / df['Close']).iloc[-20:].mean() * 100)
        if adr < 6.0:
            return None

        pivot  = find_pivot_candle(df)
        state  = classify_case(df, pivot)
        name   = get_stock_name(ticker, market)

        last_close = float(df['Close'].iloc[-1])
        prev_close = float(df['Close'].iloc[-2])
        change_pct = (last_close - prev_close) / prev_close * 100

        pivot_date_str = str(pivot['date'].date()) if pivot else ''
        pivot_vol_r    = pivot['vol_ratio'] if pivot else 0.0
        days_since     = (
            int(np.busday_count(pivot['date'].date(), df.index[-1].date()))
            if pivot else 0
        )

        if pivot:
            entry_price    = round(pivot['high'], 2)
            stop_price     = round(pivot['midline'], 2)
            risk_pct       = round((entry_price - stop_price) / entry_price * 100, 1)
            near_entry_pct = round((last_close / entry_price - 1) * 100, 2)
            pivot_pos  = df.index.get_loc(pivot['date'])
            start_pos  = max(0, pivot_pos - 65)
            prior_low  = float(df['Low'].iloc[start_pos:pivot_pos].min()) if pivot_pos > 0 else 0.0
            prior_move_pct = round((pivot['high'] / prior_low - 1) * 100, 0) if prior_low > 0 else 0.0
        else:
            entry_price = stop_price = risk_pct = near_entry_pct = prior_move_pct = 0.0

        return {
            'Ticker':        ticker,
            '종목명':        name,
            '상태':          state,
            'Close':         round(last_close, 2),
            '타점':          entry_price,
            '현재→타점%':    near_entry_pct,
            '손절':          stop_price,
            '리스크%':       risk_pct,
            '이전상승%':     prior_move_pct,
            '등락%':         round(change_pct, 2),
            '횡보일수':      days_since,
            '기준봉거래량비': round(pivot_vol_r, 1),
            'MA점수':        _ma_score(df, last_close),
            '고점대비%':     calc_pct_from_52w_high(df),
            '기준봉일':      pivot_date_str,
            'ADR%':          round(adr, 1),
        }
    except Exception:
        return None


_ROW_SCHEMA_VER = 4  # 컬럼 구조 변경 시 증가 → 구캐시 자동 무효화

@st.cache_data(ttl=3600)
def _build_10ema_rows(tickers_tuple: tuple, market: str, schema_ver: int = _ROW_SCHEMA_VER) -> list:
    rows = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_process_one, t, market): t for t in tickers_tuple}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                rows.append(result)

    rows.sort(key=lambda r: (
        STATE_ORDER.get(r['상태'], 99),
        -(r['현재→타점%'] or -99),   # 셋업 내: 타점에 가까울수록 먼저 (0에 가까운 순)
        -(r['이전상승%'] or 0),
    ))
    return rows


def render_10ema_tab(market: str, label: str):
    from data.universe import get_kr_universe, get_us_universe

    with st.spinner(f'{label} 유니버스 로딩 중...'):
        tickers = get_us_universe() if market == 'US' else get_kr_universe(market)

    if not tickers:
        st.warning('유니버스를 불러오지 못했습니다. 잠시 후 새로고침 해주세요.')
        return

    with st.expander('가이드', expanded=False):
        st.caption('상태')
        cols = st.columns(3)
        cols[0].markdown('🟢 **셋업**\n\n타이트 횡보 3~40일\n거래량 수축 · 10EMA 서핑\n\n-> 기준봉 고가 돌파 시 ORH 매수')
        cols[1].markdown('🟡 **형성중**\n\n기준봉은 있으나\n베이스 조건 미충족\n\n-> 지켜볼 종목')
        cols[2].markdown('🔵 **돌파완료**\n\nADR 1.5배+ 초과\nor 고가 위 누적 5거래일\n\n-> 추격 불가')
        cols2 = st.columns(4)
        cols2[0].markdown('🔴 **10EMA이탈**\n\n10EMA 아래\n연속 2거래일\n\n-> 셋업 무효')
        cols2[1].markdown('🔴 **중간선이탈**\n\n기준봉 중간선 아래\n연속 2거래일\n\n-> 셋업 무효')
        cols2[2].markdown('🔴 **이탈**\n\n기준봉 저가\n하방 이탈\n\n-> 셋업 무효')
        cols2[3].markdown('🔴 **없음**\n\n최근 3개월 내\n기준봉 미탐지')

        st.divider()
        st.caption('기준봉이란?')
        st.markdown(
            '**거래량 1.5배+** 폭발과 함께 저항을 돌파한 캔들(손바뀜).  \n'
            '종가가 당일 고저 범위의 **상단 70% 이상**에서 마감(윗꼬리 없이 강하게 닫힘).  \n'
            '**10EMA · 21EMA · 50MA 정배열** 상태에서 발생해야 유효.  \n'
            '이 봉의 **고가 = 타점**.'
        )

        st.divider()
        st.caption('컬럼')
        ca, cb = st.columns(2)
        ca.markdown(
            '**기준봉일** — 기준봉 발생 날짜  \n'
            '**타점** — 기준봉 고가, ORH 매수 진입가  \n'
            '**현재→타점%** — 타점까지 남은 거리 *(-5% 이내 주목)*  \n'
            '**횡보일수** — 기준봉 이후 경과 거래일 *(3~40일)*'
        )
        cb.markdown(
            '**이전상승%** — 기준봉 전 3개월 저점 대비 고가 상승폭 *(Prior Move — 30%+ 필수)*  \n'
            '**ADR%** — 최근 20일 평균 일일 변동폭 *(6%+ 필터 적용)*'
        )

        st.divider()
        if st.button('🔄 재스캔', key=f'rescan_10ema_{market}', help='전 종목 재스캔 — 수 분 소요'):
            _build_10ema_rows.clear()
            st.rerun()

    with st.spinner(f'{label} 스캔 중... {len(tickers)}개 종목 (첫 로드 시 수 분 소요)'):
        rows = _build_10ema_rows(tuple(sorted(tickers)), market, _ROW_SCHEMA_VER)

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    # 상단 요약 메트릭
    setup_rows  = [r for r in rows if r['상태'] == '셋업']
    near_rows   = [r for r in setup_rows if abs(r['현재→타점%'] or 99) <= 5]
    m1, m2, m3 = st.columns(3)
    m1.metric('🎯 셋업 완성', f'{len(setup_rows)}개')
    m2.metric('⚡ 타점 5% 이내', f'{len(near_rows)}개')
    m3.metric('📊 스캔', f'{len(tickers)}개')

    # 표시 필터
    col_a, col_b = st.columns(2)
    show_forming = col_a.checkbox('🟡 형성중 포함', value=False, key=f'show_forming_{market}')
    show_failed  = col_b.checkbox('🔴 이탈 종목 보기', value=False, key=f'show_failed_{market}')

    if show_failed:
        display_rows = rows
    elif show_forming:
        display_rows = [r for r in rows if r['상태'] in ('셋업', '형성중')]
    else:
        display_rows = setup_rows

    if not display_rows:
        st.info('현재 셋업 완성 종목 없음. "형성중 포함"을 체크하면 더 넓게 볼 수 있습니다.')
        return

    display_df = pd.DataFrame([{
        '티커 | 종목명':  f"{r['Ticker']} | {r['종목명']}",
        '상태':           STATE_BADGE.get(r['상태'], r['상태']),
        '기준봉일':       r['기준봉일'],
        '타점':           r['타점'],
        '현재→타점%':     r['현재→타점%'],
        '횡보일수':       r['횡보일수'],
        '이전상승%':      r['이전상승%'],
        'ADR%':           r['ADR%'],
    } for r in display_rows])

    if market.startswith('KR'):
        price_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        price_fmt = "value == null ? '' : '$' + value.toFixed(2)"

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter', pinned='left', minWidth=170)
    gb.configure_column('상태',  filter='agSetColumnFilter', minWidth=120)
    gb.configure_column('타점',  filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=price_fmt)
    gb.configure_column('기준봉일', filter='agTextColumnFilter')
    for col in ['현재→타점%', '이전상승%', '횡보일수', 'ADR%']:
        gb.configure_column(col, filter='agNumberColumnFilter', type=['numericColumn'])
    gb.configure_grid_options(localeText=KO_LOCALE)

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.NO_UPDATE,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=min(90 + len(display_df) * 36, 800),
        fit_columns_on_grid_load=False,
    )

    # 셋업 완성 종목 배너
    if setup_rows:
        lines = []
        for r in setup_rows:
            pct = r['현재→타점%'] or 0
            flag = '⚡' if abs(pct) <= 5 else '📌'
            lines.append(
                f"{flag} **{r['Ticker']}** {r['종목명']} &nbsp;|&nbsp; "
                f"타점 `{r['타점']}` &nbsp; 손절 `{r['손절']}` &nbsp; "
                f"리스크 **{r['리스크%']}%** &nbsp; 이전상승 **{r['이전상승%']:.0f}%** &nbsp; "
                f"타점까지 **{pct:+.1f}%** &nbsp; MA **{r['MA점수']}/5**"
            )
        st.success('🎯 **셋업 완성** (⚡ = 타점 5% 이내)  \n' + '  \n'.join(lines), icon=None)
