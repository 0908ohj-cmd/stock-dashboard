import base64
import json
import pathlib
import requests
import streamlit as st
from data.fetcher import parse_tradingview_csv, parse_ticker_txt, fetch_daily, fetch_intraday, fetch_index_daily
from ui.index_panel import render_index_panel
from ui.watchlist import render_watchlist_tab, _fetch_index_cached
from ui.watchlist_10ema import render_10ema_tab
from ui.charts import daily_chart, intraday_chart

GITHUB_REPO = "0908ohj-cmd/stock-dashboard"


def _github_save(filename: str, content: str) -> None:
    """GITHUB_TOKEN이 secrets에 있으면 파일을 GitHub에 자동 커밋."""
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        if not token:
            return
        path = f"data/saved/{filename}"
        url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
        hdrs = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        r    = requests.get(url, headers=hdrs, timeout=5)
        sha  = r.json().get("sha") if r.ok else None
        body = {"message": f"auto: {filename}",
                "content": base64.b64encode(content.encode()).decode()}
        if sha:
            body["sha"] = sha
        requests.put(url, json=body, headers=hdrs, timeout=10)
    except Exception:
        pass

SAVED_DIR = pathlib.Path(__file__).parent / 'data' / 'saved'
SAVED_DIR.mkdir(exist_ok=True)

SAVED_PATHS = {
    'KR_KOSPI':    SAVED_DIR / 'kospi.tickers',
    'KR_KOSDAQ':   SAVED_DIR / 'kosdaq.tickers',
    'US':          SAVED_DIR / 'us.tickers',
    '10EMA_KOSPI': SAVED_DIR / '10ema_kospi.tickers',
    '10EMA_KOSDAQ':SAVED_DIR / '10ema_kosdaq.tickers',
    '10EMA_US':    SAVED_DIR / '10ema_us.tickers',
}

st.set_page_config(
    page_title='Stock Watchlist',
    page_icon='📈',
    layout='wide',
    initial_sidebar_state='expanded',
)

# AgGrid 첫 탭 레이아웃 버그 방지 — 세션 최초 1회만 재실행
if 'grid_initialized' not in st.session_state:
    st.session_state['grid_initialized'] = True
    st.rerun()

INDEX_FOR_MARKET = {
    'KR_KOSPI':  'KOSPI',
    'KR_KOSDAQ': 'KOSDAQ',
    'US':        'NASDAQ',
}

# ── 사이드바 ──────────────────────────────────────────────
with st.sidebar:
    st.title('📈 Stock Watchlist')
    st.caption('TradingView 스크리너 → Export → CSV 업로드')
    st.divider()

    st.markdown('**🇰🇷 한국 주식**')
    kospi_file  = st.file_uploader('KOSPI (CSV 또는 TXT)', type=['csv', 'txt'], key='kospi_csv')
    kosdaq_file = st.file_uploader('KOSDAQ (CSV 또는 TXT)', type=['csv', 'txt'], key='kosdaq_csv')
    st.divider()
    st.markdown('**🇺🇸 미국 주식**')
    us_file = st.file_uploader('US (CSV 또는 TXT)', type=['csv', 'txt'], key='us_csv')
    st.divider()
    st.markdown('**📈 10EMA 강세장**')
    ema10_kospi_file  = st.file_uploader('10EMA 코스피 (CSV 또는 TXT)', type=['csv', 'txt'], key='10ema_kospi')
    ema10_kosdaq_file = st.file_uploader('10EMA 코스닥 (CSV 또는 TXT)', type=['csv', 'txt'], key='10ema_kosdaq')
    ema10_us_file     = st.file_uploader('10EMA 미장 (CSV 또는 TXT)',   type=['csv', 'txt'], key='10ema_us')
    st.divider()
    if st.button('🔄 새로고침', use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption('⚠️ 주가 데이터는 15분 지연 (무료 API)')
    st.divider()
    st.markdown('**💾 티커 백업**')
    backup_restore_file = st.file_uploader('백업 복원 (JSON)', type=['json'], key='backup_restore')

# ── 종목 파싱 ─────────────────────────────────────────────
kr_kospi, kr_kosdaq, us_tickers = [], [], []
ema10_kospi_tickers, ema10_kosdaq_tickers, ema10_us_tickers = [], [], []

for uploaded, key, name in [
    (kospi_file,       'KR_KOSPI',    'KOSPI'),
    (kosdaq_file,      'KR_KOSDAQ',   'KOSDAQ'),
    (us_file,          'US',          'US'),
    (ema10_kospi_file, '10EMA_KOSPI', '10EMA 코스피'),
    (ema10_kosdaq_file,'10EMA_KOSDAQ','10EMA 코스닥'),
    (ema10_us_file,    '10EMA_US',    '10EMA 미장'),
]:
    saved_path = SAVED_PATHS[key]

    if uploaded:
        try:
            import io
            raw = uploaded.read()
            fname = uploaded.name
            if fname.endswith('.txt'):
                tickers_parsed = parse_ticker_txt(raw.decode('utf-8', errors='ignore'))
            else:
                df = parse_tradingview_csv(io.BytesIO(raw))
                tickers_parsed = df['Ticker'].dropna().astype(str).tolist()
            content = '\n'.join(tickers_parsed)
            saved_path.write_text(content, encoding='utf-8')
            _github_save(saved_path.name, content)
        except Exception as e:
            st.sidebar.error(f'파일 오류: {e}')

    if saved_path.exists():
        try:
            tickers = [t for t in saved_path.read_text(encoding='utf-8').splitlines() if t.strip()]
            if key == 'KR_KOSPI':       kr_kospi            = tickers
            elif key == 'KR_KOSDAQ':    kr_kosdaq           = tickers
            elif key == 'US':           us_tickers          = tickers
            elif key == '10EMA_KOSPI':  ema10_kospi_tickers = tickers
            elif key == '10EMA_KOSDAQ': ema10_kosdaq_tickers= tickers
            else:                       ema10_us_tickers    = tickers
            label = f'{name} {len(tickers)}개' + ('' if uploaded else ' (저장됨)')
            st.sidebar.success(label)
        except Exception as e:
            st.sidebar.error(f'파일 오류: {e}')

# ── 백업 복원 처리 ────────────────────────────────────────
if backup_restore_file:
    try:
        backup = json.loads(backup_restore_file.read().decode('utf-8'))
        # 구버전 키(10EMA_KR) 호환
        if '10EMA_KR' in backup and '10EMA_KOSPI' not in backup:
            backup['10EMA_KOSPI'] = backup.pop('10EMA_KR')
        for key, tickers_list in backup.items():
            if key in SAVED_PATHS and isinstance(tickers_list, list):
                content = '\n'.join(t for t in tickers_list if t)
                SAVED_PATHS[key].write_text(content, encoding='utf-8')
                _github_save(SAVED_PATHS[key].name, content)
        st.sidebar.success('백업 복원 완료! 새로고침됩니다.')
        st.rerun()
    except Exception as e:
        st.sidebar.error(f'백업 복원 오류: {e}')

# ── 백업 다운로드 버튼 ────────────────────────────────────
_any = kr_kospi or kr_kosdaq or us_tickers or ema10_kospi_tickers or ema10_kosdaq_tickers or ema10_us_tickers
if _any:
    _backup_json = json.dumps({
        'KR_KOSPI':    kr_kospi,
        'KR_KOSDAQ':   kr_kosdaq,
        'US':          us_tickers,
        '10EMA_KOSPI': ema10_kospi_tickers,
        '10EMA_KOSDAQ':ema10_kosdaq_tickers,
        '10EMA_US':    ema10_us_tickers,
    }, ensure_ascii=False)
    st.sidebar.download_button(
        '⬇️ 티커 백업 다운로드',
        data=_backup_json,
        file_name='watchlist_backup.json',
        mime='application/json',
        use_container_width=True,
    )

# ── 지수 패널 ─────────────────────────────────────────────
render_index_panel()
st.divider()

# ── 와치리스트 ────────────────────────────────────────────
st.subheader('와치리스트')
tab_kospi, tab_kosdaq, tab_10ema_kospi, tab_10ema_kosdaq, tab_us, tab_10ema_us = st.tabs([
    '🇰🇷 KOSPI', '🇰🇷 KOSDAQ', '📈 10EMA 코스피', '📈 10EMA 코스닥', '🇺🇸 나스닥', '📈 10EMA 미장'
])
with tab_kospi:
    render_watchlist_tab(kr_kospi, 'KR_KOSPI', 'KOSPI')
with tab_kosdaq:
    render_watchlist_tab(kr_kosdaq, 'KR_KOSDAQ', 'KOSDAQ')
with tab_10ema_kospi:
    render_10ema_tab(ema10_kospi_tickers, 'KR_KOSPI', '10EMA 코스피')
with tab_10ema_kosdaq:
    render_10ema_tab(ema10_kosdaq_tickers, 'KR_KOSDAQ', '10EMA 코스닥')
with tab_us:
    render_watchlist_tab(us_tickers, 'US', '나스닥')
with tab_10ema_us:
    render_10ema_tab(ema10_us_tickers, 'US', '10EMA 미장')

# ── 종목 일봉 차트 ────────────────────────────────────────
if st.session_state.get('selected_ticker'):
    ticker     = st.session_state['selected_ticker']
    market     = st.session_state.get('selected_market', 'US')
    index_name = INDEX_FOR_MARKET.get(market, 'QQQ')

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        with st.spinner('일봉 로드 중...'):
            df_daily = fetch_daily(ticker, market=market)
            idx_df   = _fetch_index_cached(index_name)
        if not df_daily.empty:
            st.plotly_chart(daily_chart(df_daily, ticker, index_df=idx_df), use_container_width=True)
    with col2:
        with st.spinner('5분봉 로드 중...'):
            df_5m = fetch_intraday(ticker, market=market)
        if not df_5m.empty:
            st.plotly_chart(intraday_chart(df_5m, ticker, market=market), use_container_width=True)
