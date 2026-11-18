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


# ── 주도주 점수 빈칸 사유 (#44 M20) ───────────────────────
# 하락·상승국면 RS/ADR·주도주 점수는 lead_scores 대상(주도섹터 유니버스 안 · 분류 완료 · 데이터 정상)에만
# 나온다. US 스크리너로 새로 올린 종목, 분류 전·미분류 종목은 아무 표시 없이 빈칸이 돼 표 아래로 밀렸다.

def test_lead_gap_outside_universe():
    """주도섹터 유니버스 밖(US 210종목 밖) 종목은 '대상 밖'."""
    reasons = wlg.lead_gap_reasons(['NVDA', 'NEW1'], scored={'NVDA'}, universe={'NVDA'},
                                   theme_of={'NVDA': 'AI_SEMI'})

    assert reasons == {'NEW1': wlg.GAP_OUTSIDE}


def test_lead_gap_unclassified():
    """유니버스 안이지만 분류 기록이 없거나 UNCLASSIFIED면 '미분류'."""
    reasons = wlg.lead_gap_reasons(['A', 'B', 'C'], scored={'A'}, universe={'A', 'B', 'C'},
                                   theme_of={'A': 'X', 'B': 'UNCLASSIFIED'})

    assert reasons == {'B': wlg.GAP_UNCLASSIFIED, 'C': wlg.GAP_UNCLASSIFIED}


def test_lead_gap_classified_member_without_score_is_data_shortage():
    """유니버스 안·분류 완료인데 점수가 없으면 데이터 문제(기준일 종가 없음·이력 부족 등)."""
    reasons = wlg.lead_gap_reasons(['A', 'B'], scored={'A'}, universe={'A', 'B'},
                                   theme_of={'A': 'X', 'B': 'Y'})

    assert reasons == {'B': wlg.GAP_DATA}


def test_lead_gap_when_nothing_scored_is_calc_failure():
    """점수가 하나도 없으면(주도섹터 계산 불가) 분류 완료 종목도 '데이터 부족'이 아니라 '산출 불가'."""
    reasons = wlg.lead_gap_reasons(['A', 'B'], scored=set(), universe={'A', 'B'},
                                   theme_of={'A': 'X', 'B': 'UNCLASSIFIED'})

    assert reasons == {'A': wlg.GAP_NO_CALC, 'B': wlg.GAP_UNCLASSIFIED}


def test_lead_gap_without_universe_skips_membership_check():
    """universe=None(유니버스를 못 읽음)이면 '대상 밖'으로 단정하지 않는다."""
    reasons = wlg.lead_gap_reasons(['A'], scored={'Z'}, universe=None, theme_of={'A': 'X'})

    assert reasons == {'A': wlg.GAP_DATA}


def test_lead_gap_caption_counts_by_reason():
    reasons = {'A': wlg.GAP_OUTSIDE, 'B': wlg.GAP_OUTSIDE, 'C': wlg.GAP_UNCLASSIFIED}

    cap = wlg.lead_gap_caption(reasons, total=10)

    assert '3종목' in cap and '10종목 중' in cap
    assert f'{wlg.GAP_OUTSIDE} 2' in cap and f'{wlg.GAP_UNCLASSIFIED} 1' in cap
    assert wlg.GAP_DATA not in cap                                  # 0건 사유는 늘어놓지 않는다


def test_lead_gap_caption_none_without_gaps():
    assert wlg.lead_gap_caption({}, total=10) is None


# ── 5분봉 빈 결과 사유 (#39 M14) ──────────────────────────
# 5분봉이 비면 원인과 무관하게 '찐반등일이 60일 초과'라고 표시해, 레이트리밋·일시 실패로 빈 응답이 와도
# 기간 문제로 오해하게 했다.
from datetime import datetime  # noqa: E402

import pandas as pd  # noqa: E402

TODAY = datetime(2026, 11, 14, 10, 0)


def test_intraday_reason_none_when_both_present():
    assert wlg.intraday_empty_reason(pd.Timestamp('2026-11-05'), TODAY, False, False, 'NVDA', 'NASDAQ') is None


def test_intraday_reason_too_old_only_beyond_yahoo_window():
    """야후 5분봉은 최근 60일만 — fetcher와 같은 기준(59일 초과)일 때만 기간 사유."""
    old = wlg.intraday_empty_reason(pd.Timestamp('2026-08-03'), TODAY, True, True, 'NVDA', 'NASDAQ')
    assert '60일' in old and '2026-08-03' in old

    edge = wlg.intraday_empty_reason(pd.Timestamp('2026-09-16'), TODAY, True, True, 'NVDA', 'NASDAQ')  # 59일 전
    assert '60일' not in edge


def test_intraday_reason_recent_empty_is_fetch_failure_not_age():
    """최근 날짜인데 둘 다 비면 조회 실패(일시 제한 가능) — 기간 사유가 아니다."""
    msg = wlg.intraday_empty_reason(pd.Timestamp('2026-11-05'), TODAY, True, True, 'NVDA', 'NASDAQ')

    assert '60일' not in msg and '조회 실패' in msg


def test_intraday_reason_names_the_empty_side():
    stock = wlg.intraday_empty_reason(pd.Timestamp('2026-11-05'), TODAY, True, False, 'NVDA', 'NASDAQ')
    index = wlg.intraday_empty_reason(pd.Timestamp('2026-11-05'), TODAY, False, True, 'NVDA', 'NASDAQ')

    assert 'NVDA' in stock and '60일' not in stock
    assert 'NASDAQ' in index and 'NVDA' not in index and '60일' not in index

def test_lead_gap_reasons_ADR_미달은_수치와_함께():
    from ui.watchlist_logic import lead_gap_reasons
    gap = lead_gap_reasons(['A', 'B', 'C'], {'A'}, {'A', 'B', 'C'}, {'A': 'X', 'B': 'X', 'C': 'X'},
                           {'B': 3.86}, 4.0)
    assert gap == {'B': 'ADR 미달 (3.86%<4%)', 'C': '데이터 부족'}
