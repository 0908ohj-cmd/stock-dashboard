"""주도섹터 탭 화면 보조 로직 (strategy/sector_view.py) — Streamlit 없이 검증한다."""
from strategy.sector_view import pin_asof, resolve_asof

OPTS_YESTERDAY = ['2026-11-11', '2026-11-10', '2026-11-07']
OPTS_TODAY = ['2026-11-12', '2026-11-11', '2026-11-10', '2026-11-07']


# ── 기준일: 직접 고른 날만 기억하고 나머지는 최신을 따라간다 (#42 M10) ──

def test_untouched_session_follows_new_batch():
    # 어제 배치로 접속해 기준일을 건드리지 않은 세션 — 새 배치가 들어오면 그 최신일로 바뀌어야 한다.
    # (옛 코드는 '세션에 남은 값이 선택지에 있으면 유지'라 11/11에 고정됐다)
    assert resolve_asof(OPTS_YESTERDAY, None) == '2026-11-11'
    assert resolve_asof(OPTS_TODAY, None) == '2026-11-12'


def test_user_picked_past_date_survives_new_batch():
    pinned = pin_asof('2026-11-10', latest='2026-11-11')
    assert resolve_asof(OPTS_TODAY, pinned) == '2026-11-10'


def test_picking_latest_means_follow_latest():
    # 최신일을 고른 사람은 '그 날짜'가 아니라 '최신'을 원한 것 — 다음 배치에서 같이 넘어가야 한다
    pinned = pin_asof('2026-11-11', latest='2026-11-11')
    assert resolve_asof(OPTS_TODAY, pinned) == '2026-11-12'


def test_pinned_date_that_left_the_options_falls_back_to_latest():
    # 선택지(최근 60거래일) 밖으로 밀려난 날은 고를 수 없다 — 최신일로
    assert resolve_asof(OPTS_TODAY, '2026-08-01') == '2026-11-12'
