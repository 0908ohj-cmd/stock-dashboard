"""📢 업데이트 안내 팝업 — 사이트에 처음 들어올 때(세션당 1번) 띄운다.

내용을 바꾸면 NOTICE_VERSION도 바꿀 것 — 같은 세션이라도 새 안내는 다시 뜬다.
사이드바 '📢 업데이트 안내' 버튼으로 언제든 다시 열 수 있다.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st

NOTICE_VERSION = '2026-11-12'
NOTICE_UNTIL = date(2026, 11, 19)     # 이 날까지(포함)만 자동으로 띄운다 — 이후엔 사이드바 버튼으로만

NOTICE_MD = """
**주도섹터 탭을 중심으로 변경 사항이 많습니다.**
둘러보시고 **이해가 안 가는 부분이나 피드백**이 있으면 회신 부탁드립니다.

---

**주요 변경**

- **탭 순서** — 🧭 주도섹터 → 추세추종 → 10EMA(PP). 이름도 `코스피(추세추종)`, `코스피 10EMA(PP)`로 정리
- **🧭 차기 주도 (추세추종용)** — 하락국면·상승국면 RS/ADR로 차기 주도섹터·차기 주도주를 고릅니다
- **🔥 급부상 (10EMA PP용)** — 갑자기 돈이 몰린 섹터를 잡고, 그 안에서 PP 기준봉을 만들 가능성이 높은 종목을 보여줍니다
- **추세추종 탭** — 주도주 점수·순위 열 추가 (주도섹터 탭과 같은 점수), 하락·상승국면 RS/ADR 두 열
- **10EMA 탭** — '🔥 섹터' 열 추가 (최근 6주 안에 돈이 몰렸던 섹터 소속이면 표시)
- **TradingView 내보내기** — 🎯 차기 주도주·🔥 급부상 섹션 추가, 10EMA 섹션 제외
- **자세히 보기** — 섹터별 종목·분류 수정만 남김

각 섹션 맨 위 **💡 사용 방법**에 쓰는 법을 적어 두었습니다.
"""


_COOKIE = 'notice_hide'


@st.dialog('📢 업데이트 안내', width='large')
def _dialog():
    st.markdown(NOTICE_MD)
    if st.button('확인했어요', type='primary', width='stretch'):
        st.session_state['notice_seen'] = NOTICE_VERSION
        st.rerun()
    _, hide = st.columns([3, 1])      # 오른쪽 아래에 작게
    if hide.button('7일간 보지 않기', type='tertiary', width='stretch',
                   help=f'게시가 끝나는 {NOTICE_UNTIL:%m/%d}까지 이 브라우저에서 다시 띄우지 않습니다'):
        st.session_state['notice_seen'] = NOTICE_VERSION
        st.session_state['notice_set_cookie'] = True
        st.rerun()


def _write_cookie():
    """브라우저 쿠키에 '이 버전 숨김'을 게시 종료일까지 저장 — 다음 접속 때 st.context.cookies로 읽는다.
    세션 상태는 새로고침·재접속하면 사라지므로 쿠키를 쓴다."""
    import streamlit.components.v1 as components
    end = datetime(NOTICE_UNTIL.year, NOTICE_UNTIL.month, NOTICE_UNTIL.day, 23, 59, 59, tzinfo=ZoneInfo('Asia/Seoul'))
    max_age = max(0, int((end - datetime.now(ZoneInfo('Asia/Seoul'))).total_seconds()))
    js = (f'document.cookie="{_COOKIE}={NOTICE_VERSION}; max-age={max_age}; path=/; SameSite=Lax; Secure";'
          f'try{{window.parent.document.cookie="{_COOKIE}={NOTICE_VERSION}; max-age={max_age}; path=/; SameSite=Lax; Secure";}}catch(e){{}}')
    components.html(f'<script>{js}</script>', height=0)


def _hidden_by_cookie() -> bool:
    try:
        return st.context.cookies.get(_COOKIE) == NOTICE_VERSION
    except Exception:
        return False


def show_once():
    """게시 기간 안이고, '7일간 보지 않기'를 안 눌렀고, 세션에서 이 버전 안내를 아직 안 봤으면 띄운다."""
    if st.session_state.pop('notice_set_cookie', False):
        _write_cookie()
    if datetime.now(ZoneInfo('Asia/Seoul')).date() > NOTICE_UNTIL or _hidden_by_cookie():
        return
    if st.session_state.get('notice_seen') != NOTICE_VERSION:
        st.session_state['notice_seen'] = NOTICE_VERSION    # 닫기(X)로 닫아도 다시 안 뜨게
        _dialog()


def sidebar_button():
    if st.sidebar.button('📢 업데이트 안내', width='stretch'):
        _dialog()
