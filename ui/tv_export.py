"""📤 TradingView 내보내기 — 각 탭이 등록한 후보 목록을 사이드바에서 txt 한 장으로 묶는다.

탭이 자기 후보를 `register_*`로 세션에 등록하고, 사이드바 버튼이 그것을 모은다.
사이드바를 탭보다 먼저 그리는 구조라(app.py 위→아래 실행) 버튼은 `st.empty()`
슬롯에 자리만 잡아두고, 탭이 다 돌아간 뒤 그 자리에 채워 넣는다.

문자열 조립 자체는 `strategy/tv_export.py`(Streamlit 무의존)에 있다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from data import leaderboard_store
from strategy.tv_export import build_export, export_filename, order_sections

# 배포 컨테이너(Streamlit Cloud)는 UTC다 — 그대로 쓰면 파일명 스탬프가 9시간 어긋난다
KST = ZoneInfo('Asia/Seoul')

_REGISTRY_KEY = '_tv_export_sections'

_TREND_TITLE = {
    'KR_KOSPI':  '🇰🇷 코스피 후보',
    'KR_KOSDAQ': '🇰🇷 코스닥 후보',
    'US':        '🇺🇸 나스닥 후보',
}
_EMA10_TITLE = {
    'KR_KOSPI':  '📈 10EMA 코스피',
    'KR_KOSDAQ': '📈 10EMA 코스닥',
    'US':        '📈 10EMA 나스닥',
}
_LEADERBOARD_TITLE = {
    'us': '👑 리더보드 US',
    'kr': '👑 리더보드 KR',
}


def _register(key: str, title: str, tickers, market: str) -> None:
    """섹션 하나를 세션에 등록. 같은 키를 다시 부르면 최신 값으로 덮어쓴다."""
    registry = st.session_state.setdefault(_REGISTRY_KEY, {})
    registry[key] = (title, list(tickers), market)


def register_trend(market: str, tickers) -> None:
    """추세추종 탭의 매수 후보. 후보가 없으면 빈 목록으로 등록해 섹션을 비운다.

    빈 목록도 반드시 등록해야 한다 — 등록을 건너뛰면 직전 실행에서 남은 목록이
    세션에 그대로 살아남아 이미 사라진 후보가 파일에 실린다.
    """
    _register(f'trend_{market}', _TREND_TITLE.get(market, market), tickers, market)


def register_ema10(market: str, tickers) -> None:
    """10EMA 탭의 셋업 완성 종목."""
    _register(f'ema10_{market}', _EMA10_TITLE.get(market, market), tickers, market)


def register_leaderboard() -> None:
    """리더보드 US·KR. 화면의 시장 선택과 무관하게 양쪽 모두 파일에 담는다.

    JSON 파일만 읽으므로 비용이 없고, `items` 순서(랭킹)를 그대로 유지한다.
    """
    for market, title in _LEADERBOARD_TITLE.items():
        items = leaderboard_store.load(market).get('items', [])
        tickers = [
            it.get('ticker') for it in items
            if isinstance(it, dict) and isinstance(it.get('ticker'), str)
        ]
        _register(f'leaderboard_{market}', title, tickers, market)


def render_sidebar_export(slot=None) -> None:
    """모아둔 섹션을 다운로드 버튼으로. `slot`은 사이드바에 미리 잡아둔 st.empty()."""
    container = slot.container() if slot is not None else st.container()
    with container:
        st.markdown('**📤 TradingView 내보내기**')

        sections = order_sections(st.session_state.get(_REGISTRY_KEY, {}))
        body = build_export(sections)

        if not body:
            st.caption('내보낼 종목이 없습니다 — 탭 분석이 끝나면 여기에 표시됩니다.')
            return

        # 무엇이 담겼는지 눌러보기 전에 보이게 한다. 탭이 아직 계산 중이면
        # 그 섹션은 목록에 없으므로, 이 숫자가 곧 파일 내용이다.
        lines = [
            f'· {title} {len([t for t in tickers if t])}개'
            for title, tickers, _ in sections if any(tickers)
        ]
        # 줄 끝 두 칸 + 개행이 markdown의 줄바꿈 — '\n\n'로 이으면 문단이 갈려 간격이 벌어진다
        st.caption('  \n'.join(lines))

        st.download_button(
            '⬇️ TradingView WL 내려받기',
            data=body,
            file_name=export_filename(datetime.now(KST)),
            mime='text/plain',
            use_container_width=True,
            help='TradingView 워치리스트 → 가져오기에 그대로 올릴 수 있는 txt',
        )
        # 후보 필터를 바꾼 직후엔 위 목록이 한 박자 늦는다(필터는 탭 안 fragment만
        # 다시 그린다) — 이 버튼이 전체 재실행을 걸어 목록을 지금 화면과 맞춘다
        if st.button('🔄 목록 갱신', use_container_width=True,
                     help='후보 필터를 바꿨다면 눌러서 내보낼 목록을 맞추세요'):
            st.rerun()
