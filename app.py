import streamlit as st
import pandas as pd
from data.fetcher import (
    parse_tradingview_csv, fetch_daily, fetch_intraday,
    fetch_index_daily, fetch_intraday_for_date, fetch_index_intraday_for_date,
)
from ui.index_panel import render_index_panel
from ui.watchlist import render_watchlist, _get_market_status_cached, _fetch_index_cached
from ui.charts import daily_chart
from ui.intraday_overlay import calc_intraday_strength, intraday_overlay_chart

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
            if key == 'KR_KOSPI':    kr_kospi   = tickers
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
