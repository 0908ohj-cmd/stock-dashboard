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
from strategy.rs_correction import _index_peak_date, calc_correction_rs

MIN_SECTOR_N = 3
STRONG_SCORE = 70
LOOKBACK_HIGH = 63      # 고점 근접 = 최근 63거래일(약 3개월) 고가 대비


def detect_phase(index_df: pd.DataFrame, asof) -> dict:
    idf = index_df[index_df.index <= pd.Timestamp(asof)]
    label = get_phase_label(idf, calc_adr(idf))
    st = get_market_status(idf)
    mode = 'correction' if label == 'DAY1' else 'rebound' if label.startswith('DAY') else 'uptrend'
    cs = st.get('correction_start')
    peak = _index_peak_date(idf, cs) if cs is not None else None
    jjin = st.get('jjin_date') if mode == 'rebound' else None

    def since(d):
        if d is None or d not in idf.index:
            return None
        return 100 * (float(idf['Close'].iloc[-1]) / float(idf.loc[d, 'Close']) - 1)

    return {'mode': mode, 'label': label, 'correctionStart': cs, 'peakDate': peak, 'jjinDate': jjin,
            'indexSincePeak': since(peak), 'indexSinceJjin': since(jjin)}


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
        corr = calc_correction_rs(df, idf, phase['correctionStart'], None, phase['peakDate'])
        r['sincePeakExcess'] = corr['excess_pct']           # 기산점 이후 지수 대비 %p
        r['sincePeakExcessAdr'] = corr['excess_adr']
        r['leadDays'] = corr['lead_days']                   # +면 지수보다 먼저 저점
        r['volRatio'] = corr['vol_ratio']                   # 상승일/하락일 평균 거래량
        hi = float(df['High'].iloc[-LOOKBACK_HIGH:].max())
        r['offHigh'] = 100 * (float(df['Close'].iloc[-1]) / hi - 1)
        s = _since_ret(df, phase['jjinDate'])
        r['sinceJjinExcess'] = None if s is None or phase['indexSinceJjin'] is None else s - phase['indexSinceJjin']

    def pct(key):
        return ss.percentile_rank({r['ticker']: r.get(key) for r in pool})

    p = {k: pct(k) for k in ('sincePeakExcess', 'sincePeakExcessAdr', 'offHigh', 'leadDays', 'volRatio',
                             'sinceJjinExcess')}

    def g(k, t):
        v = p[k][t]
        return 0.0 if v is None else v

    for r in pool:
        t = r['ticker']
        held = (g('sincePeakExcess', t) + g('sincePeakExcessAdr', t)) / 2
        if phase['mode'] == 'correction':
            parts = {'덜 빠짐': 30 * held, '고점 근접': 25 * g('offHigh', t), '저점 선행': 15 * g('leadDays', t),
                     '거래량 우위': 15 * g('volRatio', t), '21EMA 위': 15 * r['aboveEma21']}
        else:
            parts = {'덜 빠짐': 20 * held, '찐반등 후 회복': 25 * g('sinceJjinExcess', t),
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
            'pctAbove21': sum(m['aboveEma21'] for m in members) / len(members),
            'pctAbove10': sum(m['aboveEma10'] for m in members) / len(members),
            'top': sorted(members, key=lambda m: -m['nextScore'])[:3],
        }
        s['score'] = 0.6 * s['medianScore'] + 0.4 * 100 * s['strongShare']
        (ranked if len(members) >= MIN_SECTOR_N else thin).append(s)
    ranked.sort(key=lambda s: -s['score'])
    thin.sort(key=lambda s: -s['score'])
    return ranked, thin
