import pandas as pd
import numpy as np
import pytest
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope


def _make_df(closes, highs=None, lows=None, volumes=None, start='2026-01-01'):
    n = len(closes)
    dates = pd.date_range(start, periods=n, freq='B')
    highs   = highs   or [c * 1.01 for c in closes]
    lows    = lows    or [c * 0.99 for c in closes]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame({
        'Open':   closes,
        'High':   highs,
        'Low':    lows,
        'Close':  closes,
        'Volume': volumes,
    }, index=dates)


def _make_breakout_df(base_closes, breakout_close, today_close,
                       base_vol=1_000_000, breakout_vol=4_000_000):
    """base_closes + breakout + today. 돌파봉은 저가를 낮게 설정해 top-30% 조건 충족.
    사전 30%+ 상승(_has_prior_move) 조건을 위해 base는 가파른 상승이어야 한다."""
    n_base = len(base_closes)
    closes  = base_closes + [breakout_close, today_close]
    highs   = [c * 1.01 for c in base_closes] + [breakout_close * 1.005, today_close * 1.01]
    lows    = [c * 0.99 for c in base_closes] + [breakout_close * 0.92, today_close * 0.99]
    volumes = [base_vol] * n_base + [breakout_vol, base_vol]
    return _make_df(closes, highs=highs, lows=lows, volumes=volumes)


# 사전 상승 조건(65거래일 내 저점 → 기준봉 고가 +30% 이상)을 충족하는 가파른 베이스
STEEP_BASE = [80 + i * 0.5 for i in range(73)]   # 80 → 116 (+45%)


def test_detects_high_volume_breakout():
    """거래량 1.5배+, 60일 고점 돌파, 정배열, 사전 30%+ 상승 → 기준봉 탐지"""
    df = _make_breakout_df(STEEP_BASE, breakout_close=120.0, today_close=120.0)
    result = find_pivot_candle(df, lookback=5)
    assert result is not None
    assert result['vol_ratio'] >= 1.5


def test_no_pivot_when_volume_insufficient():
    """거래량 1.5배 미만 → 기준봉 없음"""
    df = _make_breakout_df(STEEP_BASE, breakout_close=120.0, today_close=120.0,
                           breakout_vol=1_200_000)
    assert find_pivot_candle(df, lookback=5) is None


def test_no_pivot_when_close_not_in_top30pct():
    """종가가 레인지 하위 → 기준봉 없음 (긴 윗꼬리)"""
    closes  = STEEP_BASE + [118.0, 118.0]
    highs   = [c * 1.01 for c in STEEP_BASE] + [135.0, 119.0]   # 돌파봉에 긴 윗꼬리
    lows    = [c * 0.99 for c in STEEP_BASE] + [117.0, 117.5]
    volumes = [1_000_000] * 73 + [4_000_000, 1_000_000]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    # close=118, low=117, high=135 → (118-117)/(135-117) ≈ 0.06 < 0.7
    assert find_pivot_candle(df, lookback=5) is None


# ── 기준봉 선택 계약 ────────────────────────────────────────────────
# 후보가 여럿일 때 어느 봉을 돌려주는지가 곧 타점·손절가라, 아래 픽스처는
# 네 갈래(클러스터 안/밖, 무효화 단독/폴백, 연쇄 경계)를 한 빌더로 만든다.
# 병렬 리스트를 손으로 맞추면 오타 하나에 테스트가 조용히 무의미해진다.

PIVOT_BASE_LEN = 78
BASE_TOP = 70 + (PIVOT_BASE_LEN - 1) * 0.5     # 70 → 108.5 (+55%, 사전 상승 조건 충족)
SPIKE_VOL, CALM_VOL = 4_000_000, 1_000_000


def _pivot_df(bars):
    """상승 베이스 78봉 뒤에 bars 스펙대로 봉을 붙인 프레임.

    bars: [(종가, 기준봉여부)] — 기준봉이면 거래량 4배 + 저가를 종가의 92%로 깊게 잡아
    종가가 레인지 상위 30%에 들도록 만든다(_close_in_top30 충족).
    반환 프레임에서 베이스 이후 첫 봉의 위치는 항상 PIVOT_BASE_LEN(=78).
    """
    base   = [70 + i * 0.5 for i in range(PIVOT_BASE_LEN)]
    closes = list(base)
    highs  = [c * 1.01 for c in base]
    lows   = [c * 0.99 for c in base]
    vols   = [CALM_VOL] * PIVOT_BASE_LEN
    for close, is_spike in bars:
        closes.append(close)
        highs.append(close * 1.005)
        lows.append(close * (0.92 if is_spike else 0.995))
        vols.append(SPIKE_VOL if is_spike else CALM_VOL)
    return _make_df(closes, highs=highs, lows=lows, volumes=vols)


def _shelf(start, n=12, step=0.1):
    """기준봉 저가 위에서 도는 횡보 선반 — 클러스터 간격을 벌리는 용도."""
    return [(start + i * step, False) for i in range(n)]


def test_picks_first_bar_within_cluster_even_if_later_bar_has_more_volume():
    """10거래일 이내 연속 후보는 같은 상승 흐름 → 거래량이 더 큰 후행봉이 아니라 첫 봉 선택.

    타점이 기준봉 고가라 후행봉을 고르면 실제보다 높은 자리에서 진입하게 된다
    (TWST 8/10 오선택 사례 → 4c62b9e에서 클러스터 필터 도입).
    """
    b1 = BASE_TOP * 1.05
    b2 = b1 * 1.05
    df = _pivot_df([(b1, True), (b1 * 0.99, False), (b1 * 0.99, False),
                    (b2, True), (b2 * 0.99, False)])
    df.iloc[PIVOT_BASE_LEN + 3, df.columns.get_loc('Volume')] = 6_000_000  # 후행봉이 더 큰 거래량

    result = find_pivot_candle(df, lookback=10)

    assert result is not None
    assert result['date'] == df.index[PIVOT_BASE_LEN]          # 후행봉(81)이 아니라 첫 봉
    assert result['high'] == pytest.approx(b1 * 1.005)
    # vol_ratio는 반환된 봉의 값이어야 한다 — 클러스터 최댓값(6.0)을 실어 보내면
    # UI '기준봉거래량비' 열이 같은 행의 날짜·타점과 다른 봉을 가리키게 된다
    assert result['vol_ratio'] == pytest.approx(4.0)


def test_picks_latest_cluster_when_candidates_far_apart():
    """10거래일을 넘겨 떨어진 후보는 별개 흐름 → 거래량과 무관하게 최신 클러스터 선택."""
    b1 = BASE_TOP * 1.05
    shelf = _shelf(b1 * 1.01)
    b2 = shelf[-1][0] * 1.06
    df = _pivot_df([(b1, True)] + shelf + [(b2, True), (b2 * 0.995, False)])
    df.iloc[PIVOT_BASE_LEN, df.columns.get_loc('Volume')] = 6_000_000      # 앞 후보가 더 큰 거래량

    result = find_pivot_candle(df, lookback=20)

    assert result is not None
    assert result['date'] == df.index[PIVOT_BASE_LEN + 13]     # 거래량 큰 b1이 아니라 최신 b2


def test_cluster_boundary_is_measured_from_cluster_head_not_previous_candidate():
    """현행 경계 규칙 고정: 간격을 '직전 후보'가 아니라 '클러스터 첫 봉'에서 잰다.

    후보 78/85/92는 이웃 간격이 모두 7거래일이라 연쇄로 보면 한 흐름이지만,
    첫 봉 기준이면 92-78=14 > 10이라 {78}/{92}로 갈려 92가 선택된다.
    어느 쪽이 의도인지는 미확정이라 고치지 않고 현행 동작만 박아둔다 —
    연쇄 방식으로 바꾸면 이 테스트가 깨져 변경이 의도적임을 드러낸다.
    """
    bars, price = [], BASE_TOP
    for offset in range(16):
        is_spike = (PIVOT_BASE_LEN + offset) in (78, 85, 92)
        price *= 1.05 if is_spike else 1.002
        bars.append((price, is_spike))
    df = _pivot_df(bars)

    result = find_pivot_candle(df, lookback=20)

    assert result is not None
    assert result['date'] == df.index[92]      # 연쇄로 묶였다면 78이어야 한다


def test_pivot_invalidated_when_low_broken_by_later_close():
    """기준봉 저가가 이후 종가로 뚫리면 무효 — 손절선이 이미 깨진 자리를 타점으로 줄 수 없다.

    주의: 이 규칙 때문에 저가를 깬 종목은 pivot=None이 되어 classify_case가
    '저가이탈'이 아니라 '없음'을 돌려준다(=UI에 기준봉 미탐지로 표시). 별도 이슈.
    """
    b1     = BASE_TOP * 1.05
    broke  = b1 * 0.92 * 0.98                  # 기준봉 저가 아래 종가
    df = _pivot_df([(b1, True), (broke, False), (broke, False)])

    assert find_pivot_candle(df, lookback=10) is None


def test_falls_back_to_earlier_cluster_when_latest_is_invalidated():
    """최신 클러스터가 무효화되면 살아남은 이전 클러스터를 쓴다 (무효화 → 클러스터 순서)."""
    b1    = BASE_TOP * 1.05
    shelf = _shelf(b1 * 1.01)
    b2    = shelf[-1][0] * 1.06
    b1_low, b2_low = b1 * 0.92, b2 * 0.92
    broke = (b1_low + b2_low) / 2              # b2 저가 아래·b1 저가 위 → b2만 무효
    assert b1_low < broke < b2_low             # 픽스처 전제 명시
    df = _pivot_df([(b1, True)] + shelf
                   + [(b2, True), (broke, False), (broke * 1.001, False)])

    result = find_pivot_candle(df, lookback=25)

    assert result is not None
    assert result['date'] == df.index[PIVOT_BASE_LEN]          # 최신 b2는 무효 → b1로 폴백


def test_no_pivot_when_no_resistance_breakout():
    """60일 고점 돌파 없음 + 횡보 박스 아님 → 기준봉 없음"""
    closes  = [110.0] * 40 + [105.0] * 33 + [106.0, 106.0]
    volumes = [1_000_000] * 73 + [4_000_000, 1_000_000]
    df = _make_df(closes, volumes=volumes)
    assert find_pivot_candle(df, lookback=5) is None


def test_classify_returns_no_pivot_when_none():
    df = _make_df([100.0] * 30)
    assert classify_case(df, None) == '없음'


def test_classify_setup_or_forming_in_consolidation():
    """가파른 상승 + 기준봉 + 눌림 9일 → 셋업 또는 형성중 (탈락 상태는 아님)"""
    base = [60 + i * 0.8 for i in range(62)]   # 60 → 108.8 (+81%)
    breakout_close = 112.0
    consolidation  = [111.5] * 9
    closes  = base + [breakout_close] + consolidation
    volumes = [1_000_000] * 62 + [4_000_000] + [700_000] * 9
    highs   = [c * 1.01 for c in base] + [breakout_close * 1.005] + [c * 1.005 for c in consolidation]
    lows    = [c * 0.99 for c in base] + [breakout_close * 0.93] + [c * 0.995 for c in consolidation]
    df = _make_df(closes, highs=highs, lows=lows, volumes=volumes)
    pivot = find_pivot_candle(df, lookback=15)
    assert pivot is not None, "테스트 데이터가 기준봉 탐지 조건을 충족하지 못함"
    assert classify_case(df, pivot) in ('셋업', '형성중')


def test_classify_downbreak():
    closes  = [100.0] * 60 + [105.0, 100.0, 98.0]
    volumes = [1_000_000] * 60 + [4_000_000, 1_000_000, 1_000_000]
    lows    = [c * 0.99 for c in closes]
    lows[-1] = 97.0
    df = _make_df(closes, lows=lows, volumes=volumes)
    pivot = {'date': df.index[-3], 'vol_ratio': 4.0,
             'high': 105.0 * 1.01, 'low': 105.0 * 0.99,
             'midline': 105.0 * 1.0, 'close': 105.0}
    assert classify_case(df, pivot) == '저가이탈'


def _consolidation_df(post_dates):
    """60일 상승 추세 + 기준봉(마지막 상승일) + 눌림 2봉. 눌림 봉의 날짜를 조절할 수 있다."""
    closes = [100 + i * 0.5 for i in range(60)]
    dates  = list(pd.bdate_range('2026-01-01', periods=60)) + list(post_dates)
    pivot_close = closes[-1]
    post_closes = [pivot_close - 0.3] * len(post_dates)

    all_closes = closes + post_closes
    highs   = [c * 1.001 for c in closes] + [c + 0.1 for c in post_closes]
    lows    = [c * 0.99 for c in closes] + [c - 0.5 for c in post_closes]
    volumes = [1_000_000] * 60 + [700_000] * len(post_dates)   # 눌림 거래량 수축

    df = pd.DataFrame({'Open': all_closes, 'High': highs, 'Low': lows,
                       'Close': all_closes, 'Volume': volumes},
                      index=pd.DatetimeIndex(dates))
    pivot = {'date': dates[59], 'vol_ratio': 4.0,
             'high': pivot_close + 0.5, 'low': pivot_close - 2.1,
             'midline': pivot_close - 0.8, 'close': pivot_close}
    return df, pivot


def test_classify_counts_consolidation_in_trading_days_not_busdays():
    """기준봉 뒤 실제 거래일이 2일뿐이면(연휴로 달력 영업일은 5일) 아직 셋업이 아니어야 한다."""
    pivot_date = pd.bdate_range('2026-01-01', periods=60)[-1]
    post_dates = [pivot_date + pd.Timedelta(days=6), pivot_date + pd.Timedelta(days=7)]
    df, pivot = _consolidation_df(post_dates)
    assert classify_case(df, pivot) == '형성중'


def test_classify_setup_with_three_consecutive_trading_days():
    """같은 셋업이 연속 거래일 3일이면 셋업 — 거래일 카운팅 회귀 방지."""
    pivot_date = pd.bdate_range('2026-01-01', periods=60)[-1]
    post_dates = pd.bdate_range(pivot_date + pd.Timedelta(days=1), periods=3)
    df, pivot = _consolidation_df(post_dates)
    assert classify_case(df, pivot) == '셋업'


def test_10ema_slope_positive_on_uptrend():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) > 0


def test_10ema_slope_negative_on_downtrend():
    closes = [130 - i for i in range(30)]
    df = _make_df(closes)
    assert calc_10ema_slope(df) < 0
