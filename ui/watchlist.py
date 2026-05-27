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
        st.session_state['selected_ticker'] = selected_ticker
        st.session_state['selected_market'] = market
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
