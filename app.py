import base64
import hmac
import json
import os
import pathlib
import time
import requests
import streamlit as st
from data.fetcher import (
    parse_tradingview_csv, parse_ticker_txt, fetch_index_daily,
    sanitize_tickers, MAX_TICKERS_PER_MARKET,
)
from ui.index_panel import render_index_panel
from ui.watchlist import render_watchlist_tab, _fetch_index_cached
from ui.watchlist_10ema import render_10ema_tab

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

_AUTH_MAX_FAILS  = 10   # 잠금 전 허용 실패 횟수 (프로세스 전역)
_AUTH_WINDOW_SEC = 600  # 실패 집계 윈도우


@st.cache_resource
def _auth_fail_log() -> list:
    """실패 타임스탬프 목록 — cache_resource라 세션을 새로 열어도 공유(브루트포스 방지)."""
    return []


def _write_access() -> bool:
    """업로드·백업복원 등 서버 상태 변경 허용 여부.

    공개 URL로 배포되므로 익명 방문자의 쓰기를 차단한다.
    APP_PASSWORD(secrets 또는 환경변수)가 설정된 경우에만 인증 입력을 받고,
    미설정이면 쓰기 기능 자체를 비활성화한다.
    """
    try:
        expected = str(st.secrets.get('APP_PASSWORD', ''))
    except Exception:
        expected = ''
    expected = expected or os.environ.get('APP_PASSWORD', '')
    if not expected:
        st.caption('🔒 업로드/복원은 관리자 전용 — secrets에 APP_PASSWORD 설정 필요')
        return False
    if st.session_state.get('auth_ok'):
        return True

    fails = _auth_fail_log()
    now = time.time()
    fails[:] = [t for t in fails if now - t < _AUTH_WINDOW_SEC]
    if len(fails) >= _AUTH_MAX_FAILS:
        st.error('로그인 시도 횟수 초과 — 잠시 후 다시 시도하세요')
        return False

    pw = st.text_input('🔒 관리자 비밀번호', type='password', key='auth_pw')
    if not pw:
        return False
    # 같은 값이 위젯에 남아 재실행될 때 실패가 중복 집계되지 않도록 시도값 추적
    if pw != st.session_state.get('auth_pw_tried'):
        st.session_state['auth_pw_tried'] = pw
        if hmac.compare_digest(pw.encode(), expected.encode()):
            st.session_state['auth_ok'] = True
            return True
        fails.append(time.time())
    st.error('비밀번호가 올바르지 않습니다')
    return False


SAVED_DIR = pathlib.Path(__file__).parent / 'data' / 'saved'
SAVED_DIR.mkdir(exist_ok=True)

SAVED_PATHS = {
    'KR_KOSPI':  SAVED_DIR / 'kospi.tickers',
    'KR_KOSDAQ': SAVED_DIR / 'kosdaq.tickers',
    'US':        SAVED_DIR / 'us.tickers',
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

    can_write = _write_access()
    kospi_file = kosdaq_file = us_file = backup_restore_file = None
    if can_write:
        st.markdown('**📊 추세추종**')
        kospi_file        = st.file_uploader('코스피 (CSV 또는 TXT)',     type=['csv', 'txt'], key='kospi_csv')
        kosdaq_file       = st.file_uploader('코스닥 (CSV 또는 TXT)',     type=['csv', 'txt'], key='kosdaq_csv')
        us_file           = st.file_uploader('나스닥 (CSV 또는 TXT)',     type=['csv', 'txt'], key='us_csv')
    st.divider()
    if st.button('🔄 새로고침', use_container_width=True):
        st.rerun()
    st.caption('⚠️ 주가 데이터는 15분 지연 (무료 API)')
    if can_write:
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
            if fname.lower().endswith('.txt'):
                tickers_parsed = parse_ticker_txt(raw.decode('utf-8', errors='ignore'))
            else:
                df = parse_tradingview_csv(io.BytesIO(raw))
                tickers_parsed = df['Ticker'].dropna().astype(str).tolist()
            cleaned = sanitize_tickers(tickers_parsed)
            if not cleaned:
                # 빈 내용으로 기존 저장 파일을 덮어쓰지 않는다 (전체 소실 방지)
                st.sidebar.error(f'{name}: 유효한 티커가 없어 저장하지 않았습니다')
            else:
                dropped = len(tickers_parsed) - len(cleaned)
                if dropped > 0:
                    st.sidebar.warning(
                        f'{name}: {dropped}개 항목 제외됨 '
                        f'(형식 불일치 또는 {MAX_TICKERS_PER_MARKET}개 상한)'
                    )
                content = '\n'.join(cleaned)
                saved_path.write_text(content, encoding='utf-8')
                _github_save(saved_path.name, content)
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
        restored, notices = 0, 0
        for key, tickers_list in backup.items():
            if key in SAVED_PATHS and isinstance(tickers_list, list):
                cleaned = sanitize_tickers(tickers_list)
                if not cleaned:
                    if tickers_list:
                        st.sidebar.error(f'{key}: 유효한 티커가 없어 복원 건너뜀')
                        notices += 1
                    continue
                dropped = len(tickers_list) - len(cleaned)
                if dropped > 0:
                    st.sidebar.warning(f'{key}: {dropped}개 항목 제외됨 (형식 불일치 또는 상한)')
                    notices += 1
                content = '\n'.join(cleaned)
                SAVED_PATHS[key].write_text(content, encoding='utf-8')
                _github_save(SAVED_PATHS[key].name, content)
                restored += 1
        if restored and not notices:
            st.sidebar.success('백업 복원 완료! 새로고침됩니다.')
            st.rerun()
        elif restored:
            # 경고가 있으면 rerun으로 메시지를 지우지 않는다 — 새로고침은 수동
            st.sidebar.success(f'백업 복원 완료 ({restored}개 시장) — 위 경고 확인 후 새로고침하세요')
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

# ── 지수 패널 ─────────────────────────────────────────────
render_index_panel()
st.divider()

# ── 와치리스트 ────────────────────────────────────────────
st.subheader('와치리스트')
tab_kospi, tab_kosdaq, tab_us, tab_10ema_kospi, tab_10ema_kosdaq, tab_10ema_us = st.tabs([
    '🇰🇷 코스피', '🇰🇷 코스닥', '🇺🇸 나스닥', '📈 10EMA 코스피', '📈 10EMA 코스닥', '📈 10EMA 나스닥'
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

