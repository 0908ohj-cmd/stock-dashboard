"""추세추종 탭(ui/watchlist.py)의 순수 로직 — Streamlit 없이 검증한다 (#44)."""
from ui import watchlist_logic as wlg


# ── 차트 선택 (#44 M16) ───────────────────────────────────
# 선택을 행의 정수 인덱스로 저장해서, 70번째 행을 고른 채 40종목 목록을 올리면 rows[70]에서
# IndexError가 나 그 뒤 탭과 내보내기가 멈췄다. 정렬이 바뀌면 같은 인덱스가 다른 종목을 가리켰다.

def _tickers(n):
    return [f'T{i:03d}' for i in range(n)]


def test_chart_position_is_none_when_selected_ticker_left_the_list():
    """70번째 종목을 고른 뒤 40종목 목록으로 바뀌면 차트 없음(None) — IndexError가 아니다."""
    old = _tickers(80)
    selected = old[70]

    assert wlg.chart_position(_tickers(40), selected) is None


def test_chart_position_follows_ticker_when_order_changes():
    """정렬·목록이 바뀌어도 같은 종목을 가리킨다."""
    rows = ['NVDA', 'AMD', 'TSLA']
    assert wlg.chart_position(rows, 'TSLA') == 2

    assert wlg.chart_position(['TSLA', 'NVDA'], 'TSLA') == 0


def test_chart_position_without_selection_is_none():
    assert wlg.chart_position(['NVDA'], None) is None


def test_chart_position_on_empty_rows_is_none():
    assert wlg.chart_position([], 'NVDA') is None


def test_chart_step_moves_by_ticker_and_wraps():
    rows = ['A', 'B', 'C']

    assert wlg.chart_step(rows, 'B', +1) == 'C'
    assert wlg.chart_step(rows, 'C', +1) == 'A'
    assert wlg.chart_step(rows, 'A', -1) == 'C'


def test_chart_step_without_valid_selection_is_none():
    assert wlg.chart_step(['A', 'B'], 'Z', +1) is None
    assert wlg.chart_step([], 'A', +1) is None


# ── 스페이스바 이동 스크립트 (#44 M16) ─────────────────────
# 예전 스크립트는 문서 전체에서 첫 번째 '▶' 버튼을 눌러, 다른 탭의 차트가 넘어갈 수 있었다.

def test_nav_button_keys_are_per_market():
    assert wlg.nav_button_keys('US') == ('prev_US', 'next_US')
    assert wlg.nav_button_keys('KR_KOSPI') == ('prev_KR_KOSPI', 'next_KR_KOSPI')


def test_space_nav_script_targets_only_its_own_tab_buttons():
    """자기 탭 버튼의 Streamlit 키 클래스(st-key-<key>)로만 찾는다 — 화면의 아무 '▶'나 누르지 않는다."""
    js = wlg.space_nav_script('KR_KOSPI')

    assert '.st-key-next_KR_KOSPI button' in js
    assert '.st-key-prev_KR_KOSPI button' in js
    assert "querySelectorAll('button')" not in js
    assert "'▶'" not in js


def test_space_nav_script_skips_hidden_tab():
    """숨은 탭의 버튼이면 누르지 않고 키 입력도 막지 않는다(보이는 탭의 핸들러가 처리)."""
    js = wlg.space_nav_script('US')

    assert 'getClientRects' in js


def test_space_nav_scripts_register_one_handler_per_market():
    """탭마다 자기 핸들러 칸을 쓴다 — 마지막에 그린 탭의 핸들러가 다른 탭 것을 덮어쓰지 않게."""
    kospi, us = wlg.space_nav_script('KR_KOSPI'), wlg.space_nav_script('US')

    assert '"KR_KOSPI"' in kospi and '"US"' not in kospi
    assert '"US"' in us
