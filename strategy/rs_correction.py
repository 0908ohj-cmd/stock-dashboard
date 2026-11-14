import numpy as np
import pandas as pd
from strategy.indicators import calc_ma_position, calc_ema


# 거래량비 상한. 하락일이 없는 최강 종목도 이 값을 받아 실측 고비율 종목보다
# 뒤로 밀리지 않는다 (표시: 999%).
VOL_RATIO_CAP = 9.99


def _vol_ratio(df: pd.DataFrame) -> float:
    if len(df) < 4:
        return 0.0
    d = df.copy()
    d['is_up'] = d['Close'] >= d['Open']
    up   = d[d['is_up']]['Volume'].mean()
    down = d[~d['is_up']]['Volume'].mean()
    if pd.isna(up):
        return 0.0
    if pd.isna(down):
        return VOL_RATIO_CAP           # 하락일 없음 = 최강
    if down == 0:
        return 0.0                     # 하락일이 있는데 거래량 0 = 데이터 결함
    return round(min(float(up / down), VOL_RATIO_CAP), 2)


def _candle_ratio(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    bull = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open']))
        if float(r['Close']) > float(r['Open']) else 0, axis=1
    ).sum()
    bear = df.apply(
        lambda r: abs(float(r['Close']) - float(r['Open']))
        if float(r['Close']) < float(r['Open']) else 0, axis=1
    ).sum()
    if bear == 0:
        return 2.0 if bull > 0 else 1.0
    return round(float(bull / bear), 2)


def _pre_adr(stock_df: pd.DataFrame, correction_start: pd.Timestamp, period: int = 20) -> float:
    pre = stock_df[stock_df.index < correction_start].tail(period)
    if len(pre) < 2:
        return 0.0
    return float(((pre['High'] - pre['Low']) / pre['Close'] * 100).mean())


def _index_peak_date(index_df: pd.DataFrame, correction_start: pd.Timestamp, lookback: int = 63) -> pd.Timestamp:
    """
    현재 조정 시작 전 마지막 EMA21 회복일 이후의 최고점.
    직전 회복일을 찾지 못하면 lookback일 fallback.
    """
    ema21 = calc_ema(index_df, 21)
    below = (index_df['Close'] < ema21).astype(int)
    # -1 전환 = 아래→위 (EMA21 회복)
    recoveries = below.diff()[below.diff() == -1].index
    recs_before = recoveries[recoveries < correction_start]

    if len(recs_before) > 0:
        last_recovery = recs_before[-1]
        window = index_df[(index_df.index >= last_recovery) & (index_df.index <= correction_start)]
    else:
        window = index_df[index_df.index <= correction_start].tail(lookback)

    return window['Close'].idxmax()


def is_valid_rs_range(peak_date, jjin_date) -> bool:
    """RS 구간(기산점 ~ 찐반등일)이 성립하나 — 기산점이 찐반등일보다 앞이어야 한다.
    같거나 뒤면 calc_correction_rs 구간이 비어 전 종목 0(덜 빠짐 전원 동점)이 된다(#29).
    한쪽이라도 없으면 검사할 구간이 없으므로 True. 주도섹터 탭(next_leaders.detect_phase)이 쓰고,
    추세추종 탭 RS 구간 설정도 같은 검증을 재사용할 수 있게 여기 둔다."""
    if peak_date is None or jjin_date is None:
        return True
    return pd.Timestamp(peak_date) < pd.Timestamp(jjin_date)


def calc_correction_rs(
    stock_df: pd.DataFrame,
    index_df: pd.DataFrame,
    correction_start: pd.Timestamp,
    jjin_date: pd.Timestamp | None,
    custom_peak_date: pd.Timestamp | None = None,
) -> dict:
    """
    조정 구간(correction_start ~ jjin_date) RS 계산.
    jjin_date가 None이면 index_df 마지막 날까지 계산.
    custom_peak_date 지정 시 자동 전고점 계산 대신 해당 날짜를 기산점으로 사용.
    """
    empty = {
        'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0, 'excess_adr': 0.0,
        'lead_days': 0, 'ma_score': 0,
        'vol_ratio': 0.0, 'candle_ratio': 0.0, 'range_error': False,
    }

    end       = jjin_date if jjin_date is not None else index_df.index[-1]
    peak_date = custom_peak_date if custom_peak_date is not None else _index_peak_date(index_df, correction_start)

    # 기산점 ≥ 찐반등일(구간 없음) — 0을 돌려주면 전 종목 동점이 되고 RS/ADR > 0 필터가 전 종목을 조용히
    # 거른다(#40 M5). 값 대신 None + range_error로 돌려 화면이 '구간 설정 오류'를 띄우게 한다.
    # 종목 데이터 부족(아래 len < 2)은 구간 오류가 아니므로 예전처럼 0.
    if not is_valid_rs_range(peak_date, end):
        return {**{k: None for k in empty}, 'ma_score': 0, 'range_error': True}

    # RS%, 저점선행 모두 고점부터 측정
    idx_slice = index_df[(index_df.index >= peak_date) & (index_df.index <= end)]
    stk_slice = stock_df[(stock_df.index >= peak_date) & (stock_df.index <= end)]

    if len(idx_slice) < 2 or len(stk_slice) < 2:
        return empty

    idx_pct = (float(idx_slice['Close'].iloc[-1]) / float(idx_slice['Close'].iloc[0]) - 1) * 100
    stk_pct = (float(stk_slice['Close'].iloc[-1]) / float(stk_slice['Close'].iloc[0]) - 1) * 100

    idx_bottom = idx_slice['Low'].idxmin()
    stk_bottom = stk_slice['Low'].idxmin()
    # 지수 거래일 달력 기준 위치 차이 — busday는 휴장일을 거래일로 세버림.
    # 종목 저점일이 지수 달력에 없으면(결측일) 직전 지수 거래일 위치로 매핑.
    pos = idx_slice.index

    def _pos_of(d):
        return int(pos.searchsorted(d, side='right')) - 1

    lead_days = _pos_of(idx_bottom) - _pos_of(stk_bottom)

    stk_for_ma = stock_df[stock_df.index <= end]
    ma_score   = calc_ma_position(stk_for_ma) if len(stk_for_ma) >= 10 else 0

    excess_pct = round(stk_pct - idx_pct, 2)
    adr        = _pre_adr(stock_df, correction_start)
    excess_adr = round(excess_pct / adr, 2) if adr > 0 else 0.0

    return {
        'stock_pct':    round(stk_pct, 2),
        'index_pct':    round(idx_pct, 2),
        'excess_pct':   excess_pct,
        'excess_adr':   excess_adr,
        'lead_days':    lead_days,
        'ma_score':     ma_score,
        'vol_ratio':    _vol_ratio(stk_slice),
        'candle_ratio': _candle_ratio(stk_slice),
        'range_error':  False,
    }



# ── RS 구간 수동 설정 — 추세추종 탭·주도섹터 탭 공용 (#40 M5) ──────────

def _ts(d) -> pd.Timestamp | None:
    return None if d is None or d == '' else pd.Timestamp(d).normalize()


def clean_manual_rs_range(peak, jjin) -> tuple[pd.Timestamp | None, pd.Timestamp | None, list]:
    """'⚙️ RS 구간 설정' 입력 정리 → (기산점, 찐반등일, 경고 목록). 두 탭이 같은 값으로 점수를 부르게
    여기 한 곳에서 거른다. 날짜는 date·문자열·Timestamp 무엇이든 자정 Timestamp로 맞춘다.
    - 둘 다 지정했는데 기산점 ≥ 찐반등일 → 기산점을 버린다 (next_leaders.detect_phase와 같은 '기산점부터' 규칙)"""
    peak, jjin = _ts(peak), _ts(jjin)
    warns = []
    if peak is not None and jjin is not None and not is_valid_rs_range(peak, jjin):
        warns.append(f'기산점({peak:%m/%d})이 찐반등일({jjin:%m/%d})보다 앞이어야 합니다 — 기산점 설정을 무시합니다.')
        peak = None
    return peak, jjin, warns


def _snap(dates: pd.DatetimeIndex, d) -> pd.Timestamp:
    """d 이하 마지막 거래일 (휴장일 → 직전 거래일), d가 첫 거래일보다 앞이면 첫 거래일 — next_leaders._snap과 같은 규칙."""
    pos = int(dates.searchsorted(pd.Timestamp(d), side='right')) - 1
    return dates[max(pos, 0)]


def resolve_rs_range(dates: pd.DatetimeIndex, peak, jjin, auto_peak, auto_jjin
                     ) -> tuple[pd.Timestamp | None, pd.Timestamp | None, str | None]:
    """추세추종 탭 RS 구간(저점선행·양봉비 등) → (쓸 수동 기산점 | None, 찐반등일 | None, 경고 | None).
    주도섹터 탭 점수(next_leaders.detect_phase)와 같은 규칙이라 같은 수동 값이면 두 탭 구간이 같다.
    - 수동 날짜는 지수 거래일로 맞춘다 (휴장일 → 직전 거래일)
    - 기산점만 지정했는데 자동 찐반등일과 같거나 뒤 → 그 찐반등을 버리고 기산점부터 (찐반등일 None)
    - 그래도 기산점 ≥ 찐반등일이면 원인이 된 수동 값을 무시 (둘 다 지정이면 기산점부터)"""
    p = _snap(dates, peak) if peak is not None else None
    j = _snap(dates, jjin) if jjin is not None else None
    peak_d = p if p is not None else auto_peak
    jjin_d = j if j is not None else auto_jjin
    warning = None
    if p is not None and j is None and auto_jjin is not None and auto_jjin <= p:
        warning = (f"수동 기산점({p:%m/%d})이 자동 찐반등일({auto_jjin:%m/%d})과 같거나 뒤라 그 찐반등을 무시하고 "
                   "기산점부터 계산합니다.")
        jjin_d = None
    if (p is not None or j is not None) and not is_valid_rs_range(peak_d, jjin_d):
        if p is not None and j is not None:
            ignored, sub = '수동 기산점', resolve_rs_range(dates, None, jjin, auto_peak, auto_jjin)
        else:
            ignored, sub = ('수동 기산점' if p is not None else '수동 찐반등일'), (None, auto_jjin, None)
        msg = (f'기산점({peak_d:%m/%d})이 찐반등일({jjin_d:%m/%d})과 같거나 뒤라 RS 구간이 비므로 {ignored}을 '
               '무시했습니다 — 기산점은 찐반등일보다 앞이어야 합니다.')
        return sub[0], sub[1], (msg if not sub[2] else f'{msg} {sub[2]}')
    return p, jjin_d, warning
