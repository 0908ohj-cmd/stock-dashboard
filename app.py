import base64
import hashlib
import json
import pathlib
import requests
import streamlit as st
from data.fetcher import parse_tradingview_csv, parse_ticker_txt
from data import store
from ui.index_panel import render_index_panel, _load_index
from ui.watchlist import (
    render_watchlist_tab, _fetch_index_cached,
    _build_rows, _get_market_status_cached,
)
from ui.watchlist_10ema import render_10ema_tab
from ui.leaderboard import render_leaderboard_section
from ui import tv_export

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
    'KR_KOSPI':  SAVED_DIR / 'kospi.tickers',
    'KR_KOSDAQ': SAVED_DIR / 'kosdaq.tickers',
    'US':        SAVED_DIR / 'us.tickers',
}


def _clear_analysis_caches() -> None:
    _build_rows.clear()
    _fetch_index_cached.clear()
    _get_market_status_cached.clear()
    _load_index.clear()

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

    st.markdown('**📊 추세추종**')
    kospi_file        = st.file_uploader('코스피 (CSV 또는 TXT)',     type=['csv', 'txt'], key='kospi_csv')
    kosdaq_file       = st.file_uploader('코스닥 (CSV 또는 TXT)',     type=['csv', 'txt'], key='kosdaq_csv')
    us_file           = st.file_uploader('나스닥 (CSV 또는 TXT)',     type=['csv', 'txt'], key='us_csv')
    st.divider()
    if st.button('🔄 데이터 재수집', use_container_width=True,
                 help='전 시장 시세를 지금 즉시 다시 수집합니다 (비상용)'):
        st.session_state['force_refetch'] = True
    st.caption('📦 장 마감 후 배치 수집 데이터 (KR 15:45 · US 07:00 KST)')
    st.divider()
    st.markdown('**💾 티커 백업**')
    backup_restore_file = st.file_uploader('백업 복원 (JSON)', type=['json'], key='backup_restore')

# ── 종목 파싱 ─────────────────────────────────────────────
kr_kospi, kr_kosdaq, us_tickers = [], [], []

for uploaded, key, name in [
    (kospi_file,  'KR_KOSPI',  'KOSPI'),
    (kosdaq_file, 'KR_KOSDAQ', 'KOSDAQ'),
    (us_file,     'US',        'US'),
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
            # 업로드 직후 1회 수집 — file_uploader는 rerun마다 같은 파일을 반환하므로
            # 세션 시그니처 가드가 없으면 매 rerun 전량 재수집된다
            sig = hashlib.md5(raw).hexdigest()   # 내용 기반 — 같은 이름·길이의 다른 파일도 감지
            if st.session_state.get(f'uploaded_sig_{key}') != sig:
                st.session_state[f'uploaded_sig_{key}'] = sig
                with st.sidebar:
                    with st.spinner(f'{name} 시세 수집 중... ({len(tickers_parsed)}개 종목)'):
                        store.refetch_market(key, tickers_parsed)
                _clear_analysis_caches()
        except Exception as e:
            st.sidebar.error(f'파일 오류: {e}')

    if saved_path.exists():
        try:
            tickers = [t for t in saved_path.read_text(encoding='utf-8').splitlines() if t.strip()]
            if key == 'KR_KOSPI':    kr_kospi  = tickers
            elif key == 'KR_KOSDAQ': kr_kosdaq = tickers
            else:                    us_tickers = tickers
            label = f'{name} {len(tickers)}개' + ('' if uploaded else ' (저장됨)')
            st.sidebar.success(label)
        except Exception as e:
            st.sidebar.error(f'파일 오류: {e}')

# ── 백업 복원 처리 ────────────────────────────────────────
if backup_restore_file:
    try:
        backup = json.loads(backup_restore_file.read().decode('utf-8'))
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
_any = kr_kospi or kr_kosdaq or us_tickers
if _any:
    _backup_json = json.dumps({
        'KR_KOSPI':  kr_kospi,
        'KR_KOSDAQ': kr_kosdaq,
        'US':        us_tickers,
    }, ensure_ascii=False)
    st.sidebar.download_button(
        '⬇️ 티커 백업 다운로드',
        data=_backup_json,
        file_name='watchlist_backup.json',
        mime='application/json',
        use_container_width=True,
    )

# ── TradingView 내보내기 자리 ─────────────────────────────
# 내용은 탭이 전부 돌아간 뒤(스크립트 맨 아래) 이 슬롯에 채워 넣는다. 사이드바가
# 탭보다 먼저 실행되므로, 여기서 만들지 않으면 버튼이 사이드바 대신 본문 끝에 그려진다.
st.sidebar.divider()
_tv_export_slot = st.sidebar.empty()

# ── 수동 데이터 재수집 (비상용) ───────────────────────────
if st.session_state.pop('force_refetch', False):
    for key, tickers in [('KR_KOSPI', kr_kospi), ('KR_KOSDAQ', kr_kosdaq), ('US', us_tickers)]:
        if tickers:
            with st.spinner(f'{key} 재수집 중... ({len(tickers)}개 종목)'):
                store.refetch_market(key, tickers)
    with st.spinner('지수 재수집 중...'):
        store.refetch_indices()
    _clear_analysis_caches()
    st.rerun()

# ── 지수 패널 ─────────────────────────────────────────────
render_index_panel()
st.divider()

# ── 리더보드 ──────────────────────────────────────────────
# 와치리스트 탭 그룹보다 반드시 앞에서 렌더한다. Streamlit은 스크립트를 위에서 아래로
# 통째로 재실행하므로, 뒤에 두면 앞 탭들이 수백 종목 시세를 받는 동안(캐시가 비면
# 10분 이상) 이 줄에 도달하지 못해 화면이 빈 채로 남는다. 여기선 파일만 읽어 즉시 뜬다.
render_leaderboard_section()
# 리더보드는 화면의 시장 선택과 무관하게 US·KR 양쪽을 내보낸다 (JSON 읽기라 비용 없음)
tv_export.register_leaderboard()
st.divider()

# ── 와치리스트 ────────────────────────────────────────────
st.subheader('와치리스트')
tab_kospi, tab_kosdaq, tab_us, tab_10ema_kospi, tab_10ema_kosdaq, tab_10ema_us = st.tabs([
    '🇰🇷 코스피', '🇰🇷 코스닥', '🇺🇸 나스닥',
    '📈 10EMA 코스피', '📈 10EMA 코스닥', '📈 10EMA 나스닥'
])
with tab_kospi:
    render_watchlist_tab(kr_kospi, 'KR_KOSPI', 'KOSPI')
with tab_kosdaq:
    render_watchlist_tab(kr_kosdaq, 'KR_KOSDAQ', 'KOSDAQ')
with tab_us:
    render_watchlist_tab(us_tickers, 'US', '나스닥')
with tab_10ema_kospi:
    render_10ema_tab('KR_KOSPI', '10EMA 코스피')
with tab_10ema_kosdaq:
    render_10ema_tab('KR_KOSDAQ', '10EMA 코스닥')
with tab_10ema_us:
    render_10ema_tab('US', '10EMA 나스닥')

# ── TradingView 내보내기 ──────────────────────────────────
# 반드시 모든 탭 뒤에서 호출한다 — 탭들이 등록해 둔 후보 목록을 여기서 모은다.
# 그려지는 위치는 위에서 잡아둔 사이드바 슬롯이다.
tv_export.render_sidebar_export(_tv_export_slot)

