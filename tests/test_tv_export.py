from datetime import datetime

from strategy.tv_export import (
    SECTION_ORDER,
    build_export,
    build_section,
    export_filename,
    order_sections,
    summarize_sections,
    to_tv_symbol,
)


# ── to_tv_symbol ──────────────────────────────────────────
def test_kr_ticker_gets_krx_prefix():
    assert to_tv_symbol('005930', 'KR_KOSPI') == 'KRX:005930'


def test_kosdaq_ticker_also_gets_krx_prefix():
    # TradingView는 코스닥 종목도 KRX 거래소로 취급한다
    assert to_tv_symbol('247540', 'KR_KOSDAQ') == 'KRX:247540'


def test_us_ticker_has_no_prefix():
    assert to_tv_symbol('NVDA', 'US') == 'NVDA'


def test_existing_prefix_is_not_duplicated():
    assert to_tv_symbol('KRX:005930', 'KR_KOSPI') == 'KRX:005930'


def test_lowercase_market_code_still_gets_krx_prefix():
    # 리더보드 저장소는 시장을 'kr'/'us' 소문자로 다룬다 — 대소문자로 접두사가
    # 갈리면 리더보드 KR 섹션만 조용히 접두사 없이 나간다
    assert to_tv_symbol('005930', 'kr') == 'KRX:005930'


def test_surrounding_whitespace_is_trimmed():
    assert to_tv_symbol('  NVDA  ', 'US') == 'NVDA'


def test_empty_ticker_returns_none():
    assert to_tv_symbol('', 'US') is None


def test_whitespace_only_ticker_returns_none():
    assert to_tv_symbol('   ', 'KR_KOSPI') is None


def test_non_string_ticker_returns_none():
    # 시세 수집 실패 행이 None/NaN을 남길 수 있다 — 한 종목 때문에 export 전체가
    # 죽으면 안 되므로 조용히 건너뛴다
    assert to_tv_symbol(None, 'US') is None


# ── build_section ─────────────────────────────────────────
def test_section_joins_header_and_symbols_with_commas():
    line = build_section('🇰🇷 코스피 후보', ['005930', '000660'], 'KR_KOSPI')
    assert line == '###🇰🇷 코스피 후보,KRX:005930,KRX:000660'


def test_section_preserves_input_order():
    line = build_section('🇺🇸 나스닥 후보', ['NVDA', 'AAPL', 'AVGO'], 'US')
    assert line == '###🇺🇸 나스닥 후보,NVDA,AAPL,AVGO'


def test_section_drops_duplicates_keeping_first_position():
    line = build_section('US', ['NVDA', 'AAPL', 'NVDA'], 'US')
    assert line == '###US,NVDA,AAPL'


def test_section_skips_invalid_tickers_but_keeps_the_rest():
    line = build_section('US', ['NVDA', '', None, 'AAPL'], 'US')
    assert line == '###US,NVDA,AAPL'


def test_empty_ticker_list_produces_no_section():
    assert build_section('빈 섹션', [], 'US') is None


def test_section_of_only_invalid_tickers_produces_no_section():
    assert build_section('빈 섹션', ['', None], 'US') is None


# ── build_export ──────────────────────────────────────────
def test_export_puts_each_section_on_its_own_line():
    body = build_export([
        ('👑 리더보드 US', ['NVDA'], 'US'),
        ('🇰🇷 코스피 후보', ['005930'], 'KR_KOSPI'),
    ])
    assert body == '###👑 리더보드 US,NVDA\n###🇰🇷 코스피 후보,KRX:005930\n'


def test_export_omits_sections_with_no_symbols():
    # 빈 섹션이 `###제목`만 남으면 TradingView에 빈 그룹이 생긴다
    body = build_export([
        ('👑 리더보드 US', ['NVDA'], 'US'),
        ('🇰🇷 코스피 후보', [], 'KR_KOSPI'),
    ])
    assert body == '###👑 리더보드 US,NVDA\n'


def test_export_with_no_usable_section_is_empty_string():
    assert build_export([('빈 섹션', [], 'US')]) == ''


def test_export_of_nothing_is_empty_string():
    assert build_export([]) == ''


# ── order_sections ────────────────────────────────────────
def test_sections_come_out_in_fixed_order_not_registration_order():
    # 탭은 화면에 보이는 순서와 무관하게 등록된다 — 파일 안 순서는 항상 같아야
    # 사용자가 매번 같은 자리에서 같은 그룹을 본다
    registry = {
        'ema10_US': ('10EMA 나스닥', ['NVDA'], 'US'),
        'leaderboard_us': ('리더보드 US', ['AAPL'], 'US'),
        'trend_KR_KOSPI': ('코스피 후보', ['005930'], 'KR_KOSPI'),
    }
    titles = [title for title, _, _ in order_sections(registry)]
    assert titles == ['리더보드 US', '코스피 후보', '10EMA 나스닥']


def test_unregistered_sections_are_left_out():
    registry = {'leaderboard_us': ('리더보드 US', ['AAPL'], 'US')}
    assert len(order_sections(registry)) == 1


def test_empty_registry_yields_nothing():
    assert order_sections({}) == []


def test_unknown_keys_are_kept_at_the_end():
    # 나중에 탭이 늘었는데 SECTION_ORDER 갱신을 잊어도 종목이 사라지진 않게 한다
    registry = {
        'mystery_tab': ('새 탭', ['TSLA'], 'US'),
        'leaderboard_us': ('리더보드 US', ['AAPL'], 'US'),
    }
    titles = [title for title, _, _ in order_sections(registry)]
    assert titles == ['리더보드 US', '새 탭']


def test_leaderboards_precede_watchlist_sections():
    assert SECTION_ORDER.index('leaderboard_us') < SECTION_ORDER.index('trend_KR_KOSPI')


# ── summarize_sections ────────────────────────────────────
def test_summary_lists_each_section_with_its_count():
    # 버튼 툴팁용 — 누르기 전에 무엇이 담겼는지 보이게 한다
    summary = summarize_sections([
        ('👑 리더보드 US', ['NVDA', 'AMD'], 'US'),
        ('🇰🇷 코스피 후보', ['005930'], 'KR_KOSPI'),
    ])
    assert summary == '👑 리더보드 US 2개\n🇰🇷 코스피 후보 1개'


def test_summary_counts_only_usable_tickers():
    summary = summarize_sections([('👑 리더보드 US', ['NVDA', '', None], 'US')])
    assert summary == '👑 리더보드 US 1개'


def test_summary_leaves_out_empty_sections():
    summary = summarize_sections([
        ('👑 리더보드 US', ['NVDA'], 'US'),
        ('🇰🇷 코스닥 후보', [], 'KR_KOSDAQ'),
    ])
    assert summary == '👑 리더보드 US 1개'


def test_summary_of_nothing_is_empty_string():
    assert summarize_sections([]) == ''


# ── export_filename ───────────────────────────────────────
def test_filename_carries_minute_level_timestamp():
    assert export_filename(datetime(2026, 8, 15, 10, 30)) == 'watchlist_tv_20260815_1030.txt'


def test_filename_zero_pads_single_digit_parts():
    assert export_filename(datetime(2026, 1, 2, 3, 4)) == 'watchlist_tv_20260102_0304.txt'
