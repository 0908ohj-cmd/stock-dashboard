"""📤 TradingView 내보내기 — 각 탭이 등록한 후보 목록을 txt 한 장으로 묶는다.

탭이 자기 후보를 `register_*`로 세션에 등록하고, 와치리스트 제목 옆 버튼이 그것을
모은다. 버튼이 탭보다 위에 있는 구조라(app.py 위→아래 실행) `st.empty()` 슬롯에
자리만 잡아두고, 탭이 다 돌아간 뒤 그 자리에 채워 넣는다.

문자열 조립 자체는 `strategy/tv_export.py`(Streamlit 무의존)에 있다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from data import leaderboard_store
from strategy.tv_export import (
    build_export, export_filename, order_sections, summarize_sections,
)

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


def render_export_button(slot=None, key: str = 'final', pending: bool = False) -> None:
    """모아둔 섹션을 다운로드 버튼으로. `slot`은 와치리스트 제목 옆에 잡아둔 st.empty().

    **두 번 불린다.** Streamlit은 스크립트를 위에서 아래로 실행하는데, 10EMA 탭들의
    유니버스 스캔은 수 분이 걸린다 — 탭 뒤에서만 그리면 그동안 이 자리가 통째로
    비어 버린다(버튼이 아예 없는 것처럼 보인다). 그래서 탭보다 먼저 `pending=True`로
    비활성 로딩 버튼을 그려 자리를 채우고, 탭이 다 돌아간 뒤 같은 슬롯을 활성
    버튼으로 덮어쓴다.

    두 호출이 한 실행 안에 공존하므로 위젯 `key`가 서로 달라야 한다 — 같으면
    Streamlit이 DuplicateWidgetID로 앱을 통째로 죽인다.
    """
    container = slot.container() if slot is not None else st.container()
    with container:
        sections = order_sections(st.session_state.get(_REGISTRY_KEY, {}))
        body = build_export(sections)

        # 담긴 내역은 툴팁으로 — 제목 옆 좁은 자리라 목록을 펼치면 화면을 밀어낸다
        summary = summarize_sections(sections)

        # 내보내기 · 갱신 두 버튼을 한 줄에. 갱신은 아이콘만이라 폭이 훨씬 작다
        btn_col, refresh_col = st.columns([4, 1], vertical_alignment='bottom')

        with btn_col:
            if pending:
                # 아직 탭들이 후보를 등록하는 중이다. 이 시점의 목록은 리더보드 정도만
                # 찬 반쪽짜리라, 내주는 것보다 잠가 두는 편이 낫다 — 사용자가 모르고
                # 받아 가면 후보가 통째로 빠진 파일을 TradingView에 올리게 된다.
                # 탭이 끝나면 2차 렌더가 이 자리를 활성 버튼으로 덮어쓴다.
                st.button('⏳ 후보 분석 중…', disabled=True,
                          use_container_width=True,
                          key=f'tv_export_dl_{key}',
                          help='탭 분석이 끝나면 내보내기가 활성화됩니다')
            elif not body:
                st.button('📤 TradingView 내보내기', disabled=True,
                          use_container_width=True,
                          key=f'tv_export_dl_{key}',
                          help='내보낼 종목이 없습니다')
            else:
                st.download_button(
                    '📤 TradingView 내보내기',
                    data=body,
                    file_name=export_filename(datetime.now(KST)),
                    mime='text/plain',
                    use_container_width=True,
                    key=f'tv_export_dl_{key}',
                    help=f'TradingView 워치리스트 → 가져오기에 그대로 올릴 수 있는 txt\n\n{summary}',
                )

        with refresh_col:
            # 후보 필터를 바꾼 직후엔 목록이 한 박자 늦는다(필터는 탭 안 fragment만
            # 다시 그린다) — 이 버튼이 전체 재실행을 걸어 목록을 지금 화면과 맞춘다.
            # 분석 중에 누르면 처음부터 다시 도는 꼴이라 그동안은 같이 잠근다.
            if st.button('🔄', use_container_width=True,
                         disabled=pending,
                         key=f'tv_export_refresh_{key}',
                         help='후보 필터를 바꿨다면 눌러서 내보낼 목록을 맞추세요'):
                st.rerun()
