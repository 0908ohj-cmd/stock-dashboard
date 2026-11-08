"""차기 주도섹터·차기 주도주 — 지수 국면에 따라 기준을 바꾼다.

- 조정 중(DAY1): 지수가 빠지는 동안 '버틴 힘'. 덜 빠지고, 고점 근처를 지키고, 지수보다 먼저
  저점을 찍고, 상승일 거래량이 하락일보다 크고, 21EMA 위에 있는 종목이 찐반등 때 먼저 튄다.
- 찐반등 후(DAY2~7): 버틴 힘에 '찐반등일 이후 지수 대비 회복 속도'를 크게 얹는다.
  버틴 종목 중에서도 먼저 치고 나가는 종목이 진짜 주도주다.
- 상승 추세(Normal): 1주·1개월 RS와 10EMA 유지로 확인하는 주도주 점수(sector_strength)를 쓴다.

섹터는 구성 3종목 이상일 때만 순위를 매긴다. 한 종목이 섹터 순위를 정하지 않도록
점수 = 구성 종목 점수 중앙값 60% + 강한 종목(70점 이상) 비율 40%.
Streamlit 무의존.
"""
import numpy as np
import pandas as pd

from strategy import sector_strength as ss
from strategy.indicators import calc_adr
from strategy.phases import get_phase_label
from strategy.market_status import get_market_status
from strategy.rs_correction import _index_peak_date, _pre_adr, calc_correction_rs

MIN_SECTOR_N = 3
STRONG_SCORE = 70
LOOKBACK_HIGH = 63      # 고점 근접 = 최근 63거래일(약 3개월) 고가 대비


def _snap(idf: pd.DataFrame, d) -> pd.Timestamp:
    """d 이하 마지막 거래일 (휴장일·주말을 고르면 직전 거래일). d가 첫 거래일보다 앞이면 첫 거래일."""
    pos = int(idf.index.searchsorted(pd.Timestamp(d), side='right')) - 1
    return idf.index[max(pos, 0)]


def detect_phase(index_df: pd.DataFrame, asof, peak=None, jjin=None) -> dict:
    """지수 국면. peak·jjin을 주면 자동 기산점·찐반등일 대신 그 날짜를 쓴다 (추세추종 탭의 RS 구간 설정과 같은 역할).

    - jjin 지정 → 찐반등 이후 국면으로 본다 (라벨은 찐반등 다음 날부터 DAY2…, 6거래일 넘으면 '+N일')
    - peak만 지정 → 자동 국면이 상승 추세여도 그 날짜부터 '버틴 힘'을 계산한다
    """
    idf = index_df[index_df.index <= pd.Timestamp(asof)]
    label = get_phase_label(idf, calc_adr(idf))
    st = get_market_status(idf)
    mode = 'correction' if label == 'DAY1' else 'rebound' if label.startswith('DAY') else 'uptrend'
    cs = st.get('correction_start')
    auto_peak = _index_peak_date(idf, cs) if cs is not None else None
    auto_jjin = st.get('jjin_date') if mode == 'rebound' else None
    peak_d, jjin_d = auto_peak, auto_jjin

    if jjin is not None:
        jjin_d = _snap(idf, jjin)
        after = len(idf) - 1 - idf.index.get_loc(jjin_d)
        mode, label = 'rebound', (f'DAY{after + 2}' if after <= 5 else f'+{after}일')
    if peak is not None:
        peak_d = _snap(idf, peak)
        if jjin is None and mode == 'uptrend':
            mode, label = 'correction', '수동 기산점'
    if mode != 'uptrend' and peak_d is None:
        # 찐반등일만 지정했고 자동 조정이 없을 때 — 찐반등일 이전 3개월 최고 종가를 기산점으로
        win = idf[idf.index <= (jjin_d if jjin_d is not None else idf.index[-1])].tail(LOOKBACK_HIGH)
        peak_d = win['Close'].idxmax()
    if mode != 'uptrend' and (cs is None or (peak is not None and cs < peak_d)):
        cs = peak_d     # 자동 조정이 없거나 기산점을 뒤로 옮기면 ADR 기준 시점도 기산점으로

    def since(d):
        if d is None or d not in idf.index:
            return None
        return 100 * (float(idf['Close'].iloc[-1]) / float(idf.loc[d, 'Close']) - 1)

    return {'mode': mode, 'label': label, 'correctionStart': cs, 'peakDate': peak_d, 'jjinDate': jjin_d,
            'indexSincePeak': since(peak_d), 'indexSinceJjin': since(jjin_d),
            'autoPeakDate': auto_peak, 'autoJjinDate': auto_jjin,
            'manual': peak is not None or jjin is not None}


def _since_ret(df: pd.DataFrame, d) -> float | None:
    if d is None or d not in df.index:
        return None
    return 100 * (float(df['Close'].iloc[-1]) / float(df.loc[d, 'Close']) - 1)


def score_stocks(rows: list, bars: dict, index_df: pd.DataFrame, theme_of: dict, main_codes: set,
                 phase: dict, asof) -> list:
    """rows: sector_strength.stock_metrics 결과(FULL만 점수). 점수는 0~100, 같은 유니버스 안 백분위 기반."""
    asof = pd.Timestamp(asof)
    idf = index_df[index_df.index <= asof]
    pool = [r for r in rows if r['status'] == 'FULL' and theme_of.get(r['ticker']) in main_codes]
    if not pool:
        return []

    if phase['mode'] == 'uptrend':
        for r in pool:
            r['nextScore'] = r.get('leaderScore', 0) / 85 * 100
            r['nextParts'] = {}
        return pool

    for r in pool:
        df = bars[r['ticker']]
        df = df[df.index <= asof]
        # 조정 구간 = 기산점 ~ 찐반등일 (추세추종 탭 RS 구간과 동일). 조정 중이면 찐반등이 없으니 기준일까지.
        # 찐반등 이후 성과는 sinceJjinExcess가 따로 맡아서 두 항목이 겹치지 않는다.
        end = phase['jjinDate'] if phase['mode'] == 'rebound' else None
        corr = calc_correction_rs(df, idf, phase['correctionStart'], end, phase['peakDate'])
        r['sincePeakExcess'] = corr['excess_pct']           # 조정 구간 지수 대비 %p
        r['sincePeakExcessAdr'] = corr['excess_adr']
        r['leadDays'] = corr['lead_days']                   # +면 지수보다 먼저 저점
        r['volRatio'] = corr['vol_ratio']                   # 상승일/하락일 평균 거래량
        hi = float(df['High'].iloc[-LOOKBACK_HIGH:].max())
        r['offHigh'] = 100 * (float(df['Close'].iloc[-1]) / hi - 1)
        s = _since_ret(df, phase['jjinDate'])
        r['sinceJjinExcess'] = None if s is None or phase['indexSinceJjin'] is None else s - phase['indexSinceJjin']
        adr = _pre_adr(df, phase['correctionStart'])      # 기산점 쪽과 같은 ADR(조정 시작 전 20일)
        r['sinceJjinExcessAdr'] = (None if r['sinceJjinExcess'] is None or not adr
                                   else r['sinceJjinExcess'] / adr)

    def pct(key):
        return ss.percentile_rank({r['ticker']: r.get(key) for r in pool})

    # 지수 대비는 ADR 보정값만 점수에 쓴다 — 변동성 큰 종목이 %p만으로 상위를 차지하는 노이즈 제거
    p = {k: pct(k) for k in ('sincePeakExcessAdr', 'offHigh', 'leadDays', 'volRatio', 'sinceJjinExcessAdr')}

    def g(k, t):
        v = p[k][t]
        return 0.0 if v is None else v

    for r in pool:
        t = r['ticker']
        held = g('sincePeakExcessAdr', t)
        if phase['mode'] == 'correction':
            parts = {'덜 빠짐': 30 * held, '고점 근접': 25 * g('offHigh', t), '저점 선행': 15 * g('leadDays', t),
                     '거래량 우위': 15 * g('volRatio', t), '21EMA 위': 15 * r['aboveEma21']}
        else:
            parts = {'덜 빠짐': 20 * held, '찐반등 후 회복': 25 * g('sinceJjinExcessAdr', t),
                     '고점 근접': 20 * g('offHigh', t), '거래량 우위': 10 * g('volRatio', t),
                     '21EMA 위': 15 * r['aboveEma21'], '양봉 렐볼': 10 * r['hasBullishRvol15InLast5']}
        r['nextParts'] = parts
        r['nextScore'] = float(sum(parts.values()))
    return pool


def rank_sectors(pool: list) -> tuple[list, list]:
    """(순위 섹터, 표본 부족 섹터). 순위 섹터는 점수 내림차순."""
    by: dict = {}
    for r in pool:
        by.setdefault(r['primaryTheme'], []).append(r)
    ranked, thin = [], []
    for code, members in by.items():
        scores = [m['nextScore'] for m in members]
        med = lambda k: (float(np.median([m[k] for m in members if m.get(k) is not None]))
                         if any(m.get(k) is not None for m in members) else None)
        s = {
            'theme': code, 'n': len(members),
            'medianScore': float(np.median(scores)),
            'strongShare': sum(x >= STRONG_SCORE for x in scores) / len(scores),
            'medianSincePeak': med('sincePeakExcess'), 'medianOffHigh': med('offHigh'),
            'medianSinceJjin': med('sinceJjinExcess'), 'medianRs5': med('rs5Blend'),
            'medianSincePeakAdr': med('sincePeakExcessAdr'), 'medianSinceJjinAdr': med('sinceJjinExcessAdr'),
            'pctAbove21': sum(m['aboveEma21'] for m in members) / len(members),
            'pctAbove10': sum(m['aboveEma10'] for m in members) / len(members),
            'top': sorted(members, key=lambda m: -m['nextScore'])[:3],
        }
        s['score'] = 0.6 * s['medianScore'] + 0.4 * 100 * s['strongShare']
        (ranked if len(members) >= MIN_SECTOR_N else thin).append(s)
    ranked.sort(key=lambda s: -s['score'])
    thin.sort(key=lambda s: -s['score'])
    return ranked, thin
