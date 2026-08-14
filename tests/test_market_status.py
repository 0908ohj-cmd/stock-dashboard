import pandas as pd
import numpy as np
import pytest
from strategy.market_status import detect_jjin_bounce, get_market_status


def _make_df(closes, opens=None, highs=None, lows=None, volumes=None):
    n = len(closes)
    dates = pd.date_range('2026-01-01', periods=n, freq='B')
    opens = opens or closes[:]
    highs = highs or [c * 1.01 for c in closes]
    lows  = lows  or [c * 0.99 for c in closes]
    volumes = volumes or [1_000_000] * n
    return pd.DataFrame(
        {'Open': opens, 'High': highs, 'Low': lows, 'Close': closes, 'Volume': volumes},
        index=dates,
    )


def _correction_df():
    """
    25일 상승(100→124) → 10일 하락(음봉, EMA21 이탈) → 1일 찐반등.
    반등 시 EMA21≈110 > close=105 보장.
    """
    up_c  = [100 + i for i in range(25)]
    up_o  = [100 + i for i in range(25)]
    # 10일 하락: open > close (음봉), body=3
    down_c = [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]
    down_o = [127, 124, 121, 118, 115, 112, 109, 106, 103, 100]
    # 반등: close=105, open=97, body=8, prev_body=3 → cover=267%
    bounce_c, bounce_o = 105, 97
    closes  = up_c  + down_c  + [bounce_c]
    opens   = up_o  + down_o  + [bounce_o]
    highs   = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    return _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)


def test_detect_jjin_bounce_returns_none_on_plain_downtrend():
    closes = [100 - i for i in range(30)]
    df = _make_df(closes)
    assert detect_jjin_bounce(df) is None


def test_detect_jjin_bounce_detects_adr_covering_candle():
    df = _correction_df()
    result = detect_jjin_bounce(df)
    assert result is not None
    assert result['pct'] > 0
    assert result['cover_pct'] >= 70


def test_detect_jjin_bounce_gap_up_qualifies():
    """갭업도 조건 충족 시 인정"""
    up_c  = [100 + i for i in range(25)]
    up_o  = [100 + i for i in range(25)]
    down_c = [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]
    down_o = [127, 124, 121, 118, 115, 112, 109, 106, 103, 100]
    # 갭업: close=108, open=97, body=11 > prev_body(3)*0.7
    gap_c, gap_o = 108, 97
    closes  = up_c  + down_c  + [gap_c]
    opens   = up_o  + down_o  + [gap_o]
    highs   = [max(o, c) * 1.002 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.998 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    df = _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    assert detect_jjin_bounce(df) is not None


def test_detect_jjin_bounce_fails_when_body_coverage_below_70pct():
    up_c  = [100 + i for i in range(25)]
    up_o  = [100 + i for i in range(25)]
    down_c = [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]
    down_o = [127, 124, 121, 118, 115, 112, 109, 106, 103, 100]
    # 바디=2 < 이전음봉바디(3)*0.7=2.1 → 커버 부족
    bounce_c, bounce_o = 99, 97
    closes  = up_c  + down_c  + [bounce_c]
    opens   = up_o  + down_o  + [bounce_o]
    highs   = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows    = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    volumes = [1_000_000] * len(closes)
    df = _make_df(closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    assert detect_jjin_bounce(df) is None


def test_get_market_status_normal():
    closes = [100 + i for i in range(30)]
    df = _make_df(closes)
    status = get_market_status(df)
    assert status['state'] == 'normal'


def test_get_market_status_correction():
    closes = [100 + i for i in range(25)] + [80] * 5
    df = _make_df(closes)
    status = get_market_status(df)
    assert status['state'] == 'correction'
    assert status['correction_start'] is not None


def test_get_market_status_early_signal():
    df = _correction_df()
    status = get_market_status(df)
    assert status['state'] in ('early_signal', 'ftd_confirmed')
    assert status['jjin_date'] is not None
    assert status['jjin_pct'] > 0


# ── 실패한 찐반등이 이후의 새 찐반등을 가리면 안 된다 ─────────────
# detect_jjin_bounce가 조정 최저점 이후 '첫 번째' 반등만 반환하면, 그 반등이
# 실패 판정된 뒤 신저가 없이 나온 새 반등은 영원히 감지되지 않는다.

def _oc_df(pairs):
    opens  = [o for o, _ in pairs]
    closes = [c for _, c in pairs]
    highs  = [max(o, c) * 1.005 for o, c in pairs]
    lows   = [min(o, c) * 0.995 for o, c in pairs]
    return _make_df(closes, opens=opens, highs=highs, lows=lows)


_UP    = [(100 + i, 100 + i) for i in range(25)]
_DOWN  = list(zip([127, 124, 121, 118, 115, 112, 109, 106, 103, 100],
                  [124, 121, 118, 115, 112, 109, 106, 103, 100, 97]))
_B1    = [(97, 105)]                                       # 첫 찐반등
_WIN5  = [(107, 104), (106, 103), (105, 102), (104, 101), (103, 100)]  # 5거래일 미회복 → 실패
_POST  = [(102, 99), (100, 97)]                            # 실패 확정 후 조정 지속 (신저가 없음)
_B2    = [(97, 104)]                                       # 새 찐반등 (+7.2% ≥ ADR)


def test_new_jjin_detected_after_failed_jjin_without_new_low():
    """첫 찐반등 실패 후 신저가 없이 나온 새 찐반등 → early_signal로 전환돼야 한다."""
    df = _oc_df(_UP + _DOWN + _B1 + _WIN5 + _POST + _B2)
    status = get_market_status(df)
    assert status['state'] == 'early_signal'
    assert status['jjin_date'] == df.index[-1]


def test_second_failed_jjin_reports_latest_failure():
    """새 찐반등도 실패하면 correction 복귀 + 최근 실패일 보고."""
    b2_fail = [(106, 103), (105, 102), (104, 101), (103, 100), (102, 99), (101, 98)]
    df = _oc_df(_UP + _DOWN + _B1 + _WIN5 + _POST + _B2 + b2_fail)
    b2_date = df.index[-(len(b2_fail) + 1)]
    status = get_market_status(df)
    assert status['state'] == 'correction'
    assert status['failed_jjin_date'] == b2_date


_RECOV = [(104, 112), (112, 118), (118, 124)]        # EMA21 위로 회복
# B1 실패창 안 3번째 봉이 찐반등 조건 충족 (EMA21 아래에서 마감 → B1은 여전히 실패)
_WIN_CAND = [(107, 102), (102, 99), (99, 107), (107, 104), (104, 101), (101, 100)]


def test_normal_state_reports_new_jjin_not_failed_one():
    """EMA21 회복 후 보고되는 찐반등은 실패한 첫 반등이 아니라 마지막 유효 반등이어야 한다."""
    df = _oc_df(_UP + _DOWN + _B1 + _WIN5 + _POST + _B2 + _RECOV)
    b2_date = df.index[len(_UP) + len(_DOWN) + len(_B1) + len(_WIN5) + len(_POST)]
    status = get_market_status(df)
    assert status['state'] == 'normal'
    assert status['jjin_date'] == b2_date


def test_new_jjin_inside_failed_window_is_detected():
    """실패한 찐반등의 확인 대기창(DAY3~7) 안에서 나온 새 찐반등도 감지돼야 한다."""
    df = _oc_df(_UP + _DOWN + _B1 + _WIN_CAND)
    cand_date = df.index[len(_UP) + len(_DOWN) + len(_B1) + 2]
    status = get_market_status(df)
    assert status['state'] == 'early_signal'
    assert status['jjin_date'] == cand_date


def test_get_market_status_survives_duplicate_dates():
    """중복 날짜 인덱스에서도 크래시하지 않는다 (스냅샷 병합 잔재 방어)."""
    df = _oc_df(_UP + _DOWN + _B1 + _WIN5)
    jjin_date = df.index[len(_UP) + len(_DOWN)]
    df = pd.concat([df, df.loc[[jjin_date]]]).sort_index()
    status = get_market_status(df)
    assert status['state'] in ('normal', 'correction', 'early_signal')


def test_jjin_bounce_skips_incomplete_ohlc_row():
    """Close만 있고 OHL이 NaN인 불완전 행(지수 패치 잔재 등)을 찐반등으로 오검출하면 안 된다."""
    dates = pd.date_range('2026-01-01', periods=35, freq='B')
    opens  = [100.0] * 30 + [100.0, 98.0, 96.0, 94.0] + [np.nan]
    closes = [100.0] * 30 + [98.0, 96.0, 94.0, 92.0]  + [96.7]   # 마지막 행 +5.1%
    highs  = [101.0] * 30 + [100.5, 98.5, 96.5, 94.5] + [np.nan]
    lows   = [99.0]  * 30 + [97.5, 95.5, 93.5, 91.5]  + [np.nan]
    vols   = [1_000_000] * 34 + [1_300_000]
    df = pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows,
                       'Close': closes, 'Volume': vols}, index=dates)

    assert detect_jjin_bounce(df) is None
