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
둘러보시고 **이해가 안 가는 부분이나 피드백**이 있으면 회신 부탁드립니다. 🙏

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


@st.dialog('📢 업데이트 안내', width='large')
def _dialog():
    st.markdown(NOTICE_MD)
    if st.button('확인했어요', type='primary', width='stretch'):
        st.session_state['notice_seen'] = NOTICE_VERSION
        st.rerun()


def show_once():
    """게시 기간 안이고, 세션에서 이 버전 안내를 아직 안 봤으면 띄운다."""
    if datetime.now(ZoneInfo('Asia/Seoul')).date() > NOTICE_UNTIL:
        return
    if st.session_state.get('notice_seen') != NOTICE_VERSION:
        st.session_state['notice_seen'] = NOTICE_VERSION    # 닫기(X)로 닫아도 다시 안 뜨게
        _dialog()


def sidebar_button():
    if st.sidebar.button('📢 업데이트 안내', width='stretch'):
        _dialog()
