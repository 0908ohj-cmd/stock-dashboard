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
    # 상승 추세로 넘어가도 다음 DAY1(새 21EMA 이탈) 전까지는 마지막 기산점·찐반등을 유지한다
    # (get_market_status가 normal 상태에서도 둘을 넘겨준다 — 추세추종 탭과 같은 규칙)
    auto_jjin = st.get('jjin_date') if mode != 'correction' else None
    peak_d, jjin_d = auto_peak, auto_jjin

    if jjin is not None:
        jjin_d = _snap(idf, jjin)
        after = len(idf) - 1 - idf.index.get_loc(jjin_d)
        mode, label = 'rebound', (f'DAY{after + 2}' if after <= 5 else f'+{after}일')
    if peak is not None:
        peak_d = _snap(idf, peak)
        if jjin is None and jjin_d is not None and jjin_d <= peak_d:
            jjin_d = None       # 수동 기산점이 자동 찐반등보다 뒤면 그 찐반등은 무효
        if jjin_d is None and mode == 'uptrend':
            mode, label = 'correction', '수동 기산점'
    if jjin_d is not None and peak_d is None:
        # 찐반등일만 있고 자동 조정이 없을 때 — 찐반등일 이전 3개월 최고 종가를 기산점으로
        win = idf[idf.index <= jjin_d].tail(LOOKBACK_HIGH)
        peak_d = win['Close'].idxmax()
    if mode == 'correction' and peak_d is None:
        peak_d = idf.tail(LOOKBACK_HIGH)['Close'].idxmax()
    if peak_d is not None and (cs is None or (peak is not None and cs < peak_d)):
        cs = peak_d     # 자동 조정이 없거나 기산점을 뒤로 옮기면 ADR 기준 시점도 기산점으로
    # 점수 공식: 찐반등이 있으면 (찐반등 이후·상승 추세 모두) 반등 공식, 기산점만 있으면 조정 공식
    scoring = 'rebound' if jjin_d is not None else 'correction' if peak_d is not None else 'leader'

    def since(d):
        if d is None or d not in idf.index:
            return None
        return 100 * (float(idf['Close'].iloc[-1]) / float(idf.loc[d, 'Close']) - 1)

    # 반등 구간의 시작점 = 찐반등 '전날' 종가 → 찐반등 당일 상승이 포함돼 그날 저녁부터 값이 나온다
    # (5년 검증에서 반등 주도형의 효과는 찐반등 당일 세기에서 나왔다)
    jjin_base = None
    if jjin_d is not None:
        p = idf.index.get_loc(jjin_d)
        jjin_base = idf.index[p - 1] if p > 0 else jjin_d

    return {'mode': mode, 'label': label, 'scoring': scoring, 'correctionStart': cs, 'peakDate': peak_d, 'jjinDate': jjin_d,
            'jjinBase': jjin_base,
            'indexSincePeak': since(peak_d), 'indexSinceJjin': since(jjin_base),
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

    scoring = phase.get('scoring') or {'uptrend': 'leader', 'correction': 'correction'}.get(phase['mode'], 'rebound')
    if scoring == 'leader':     # 기산점·찐반등이 전혀 없을 때만 (지수 이력에 조정 자체가 없음)
        for r in pool:
            r['nextScore'] = r.get('leaderScore', 0) / 85 * 100
            r['nextParts'] = {}
        return pool

    for r in pool:
        df = bars[r['ticker']]
        df = df[df.index <= asof]
        # 조정 구간 = 기산점 ~ 찐반등일 (추세추종 탭 RS 구간과 동일). 조정 중이면 찐반등이 없으니 기준일까지.
        # 찐반등 이후 성과는 sinceJjinExcess가 따로 맡아서 두 항목이 겹치지 않는다.
        end = phase['jjinDate'] if scoring == 'rebound' else None
        corr = calc_correction_rs(df, idf, phase['correctionStart'], end, phase['peakDate'])
        r['sincePeakExcess'] = corr['excess_pct']           # 조정 구간 지수 대비 %p
        r['sincePeakExcessAdr'] = corr['excess_adr']
        r['leadDays'] = corr['lead_days']                   # +면 지수보다 먼저 저점
        r['volRatio'] = corr['vol_ratio']                   # 상승일/하락일 평균 거래량
        hi = float(df['High'].iloc[-LOOKBACK_HIGH:].max())
        r['offHigh'] = 100 * (float(df['Close'].iloc[-1]) / hi - 1)
        s = _since_ret(df, phase.get('jjinBase') or phase['jjinDate'])    # 찐반등 전날 종가부터
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
        if scoring == 'correction':
            parts = {'덜 빠짐': 30 * held, '고점 근접': 25 * g('offHigh', t), '저점 선행': 15 * g('leadDays', t),
                     '거래량 우위': 15 * g('volRatio', t), '21EMA 위': 15 * r['aboveEma21']}
        else:
            parts = {'덜 빠짐': 20 * held, '찐반등 후 회복': 25 * g('sinceJjinExcessAdr', t),
                     '고점 근접': 20 * g('offHigh', t), '거래량 우위': 10 * g('volRatio', t),
                     '21EMA 위': 15 * r['aboveEma21'], '양봉 렐볼': 10 * r['hasBullishRvol15InLast5']}
        r['nextParts'] = parts
        r['nextScore'] = float(sum(parts.values()))
    return pool


def rank_sectors(pool: list, key: str = 'nextScore') -> tuple[list, list]:
    """(순위 섹터, 표본 부족 섹터). 순위 섹터는 점수 내림차순. key: 종목 점수 필드."""
    by: dict = {}
    for r in pool:
        by.setdefault(r['primaryTheme'], []).append(r)
    ranked, thin = [], []
    for code, members in by.items():
        scores = [m[key] for m in members]
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
            'medianRotRs5': med('rotRs5'), 'medianRotAccel': med('rotAccel'), 'medianVolRatio': med('volRatio'),
            'pctBullRvol': sum(bool(m.get('hasBullishRvol15InLast5')) for m in members) / len(members),
            'top': sorted(members, key=lambda m: -m[key])[:3],
        }
        s['score'] = 0.6 * s['medianScore'] + 0.4 * 100 * s['strongShare']
        (ranked if len(members) >= MIN_SECTOR_N else thin).append(s)
    ranked.sort(key=lambda s: -s['score'])
    thin.sort(key=lambda s: -s['score'])
    return ranked, thin


# ── 순환매 — 지금 돈이 '새로' 들어오는 섹터 ───────────────────
# 차기 주도와 같은 재료(지수 대비 RS/ADR·추세·거래량·고점)를 쓰되, 기준 구간이 찐반등이 아니라
# 최근 1~2주 롤링이고, 목적에 맞게 '변화(가속)'와 '거래량 유입'에 무게를 둔다.
#   - 덜 빠짐 없음: 순환매 섹터는 보통 조정 때 약했던 곳이라 감점하면 안 잡힌다
#   - RS 가속: 이번 주 RS/ADR − 지난주 RS/ADR. 흐름이 바뀌는 순간이 순환매의 핵심
#   - 거래량 유입 25: 양봉 렐볼 + 상승일/하락일 거래량 — 돈이 들어온 흔적
#   - 고점 근접 15(차기 주도 20~25보다 낮게): 바닥권에서 올라오는 섹터도 잡히도록
ROT_WEIGHTS = {'1주 RS/ADR': 25, 'RS 가속': 15, '양봉 렐볼': 15, '거래량 우위': 10,
               '10EMA 유지': 10, '21EMA 위': 10, '고점 근접': 15}
ROT_WAS_WEAK = 50       # 2주 전 섹터 점수가 이보다 낮았고
ROT_NOW = 60            # 지금 이 이상이면 🆕 새 순환매
ROT_RISE = 15           # 그리고 2주 동안 이만큼 이상 올랐으면


def _close_ret(df: pd.DataFrame, a, b) -> float:
    return float(df.loc[b, 'Close']) / float(df.loc[a, 'Close']) - 1


def rotation_scores(rows: list, bars: dict, index_df: pd.DataFrame, theme_of: dict, main_codes: set,
                    asof) -> list:
    """종목별 순환매 점수(100). rows: 같은 기준일의 stock_metrics 결과(10EMA·21EMA·렐볼 플래그 재사용).
    ADR은 비교 구간(최근 2주) 시작 전 20일 — 급등으로 커진 변동폭이 보정을 흐리지 않게."""
    asof = pd.Timestamp(asof)
    idf = index_df[index_df.index <= asof]
    if len(idf) < 32:
        return []
    d0, d5, d10 = idf.index[-1], idf.index[-6], idf.index[-11]
    i_now, i_prev = _close_ret(idf, d5, d0), _close_ret(idf, d10, d5)
    pool = []
    for r in rows:
        t = r['ticker']
        if r['status'] != 'FULL' or theme_of.get(t) not in main_codes or t not in bars:
            continue
        df = bars[t]
        df = df[df.index <= asof]
        if any(d not in df.index for d in (d0, d5, d10)):
            continue
        pre = df[df.index < d10].tail(20)
        adr = float(((pre['High'] - pre['Low']) / pre['Close'] * 100).mean()) if len(pre) >= 10 else 0.0
        if not adr > 0:
            continue
        rs_now = 100 * (_close_ret(df, d5, d0) - i_now) / adr
        rs_prev = 100 * (_close_ret(df, d10, d5) - i_prev) / adr
        win = df[df.index > d10]
        chg = df['Close'].diff().loc[win.index]
        up, down = win['Volume'][chg > 0], win['Volume'][chg < 0]
        vol_ratio = (float(up.mean()) / float(down.mean())) if len(up) and len(down) and down.mean() > 0 \
            else (2.0 if len(up) and not len(down) else 0.0)
        hi = float(df['High'].iloc[-LOOKBACK_HIGH:].max())
        pool.append({
            'ticker': t, 'primaryTheme': theme_of[t], 'status': 'FULL',
            'rotRs5': rs_now, 'rotRsPrev5': rs_prev, 'rotAccel': rs_now - rs_prev,
            'volRatio': vol_ratio, 'offHigh': 100 * (float(df['Close'].iloc[-1]) / hi - 1),
            'aboveEma10': r['aboveEma10'], 'aboveEma21': r['aboveEma21'],
            'ema10DaysOfLast5': r['ema10DaysOfLast5'],
            'hasBullishRvol15InLast5': r['hasBullishRvol15InLast5'],
        })
    if not pool:
        return []
    p = {k: ss.percentile_rank({x['ticker']: x[k] for x in pool})
         for k in ('rotRs5', 'rotAccel', 'volRatio', 'offHigh')}
    g = lambda k, t: p[k][t] or 0.0
    w = ROT_WEIGHTS
    for x in pool:
        t = x['ticker']
        parts = {'1주 RS/ADR': w['1주 RS/ADR'] * g('rotRs5', t), 'RS 가속': w['RS 가속'] * g('rotAccel', t),
                 '양봉 렐볼': w['양봉 렐볼'] * x['hasBullishRvol15InLast5'],
                 '거래량 우위': w['거래량 우위'] * g('volRatio', t),
                 '10EMA 유지': w['10EMA 유지'] * x['ema10DaysOfLast5'] / 5,
                 '21EMA 위': w['21EMA 위'] * x['aboveEma21'], '고점 근접': w['고점 근접'] * g('offHigh', t)}
        x['rotParts'], x['rotScore'] = parts, float(sum(parts.values()))
    return pool


def rotation_sectors(now_pool: list, prev_pool: list) -> tuple[list, list]:
    """(순위 섹터, 표본 부족). 섹터 점수 = 중앙값 60% + 70점 이상 비율 40% (차기 주도섹터와 동일).
    🆕 isNew: 2주 전 점수 < 50 → 지금 ≥ 60, +15 이상. 정렬: 🆕 먼저, 그다음 점수."""
    ranked, thin = rank_sectors(now_pool, 'rotScore')
    pr, pt = rank_sectors(prev_pool, 'rotScore') if prev_pool else ([], [])
    prev = {s['theme']: s['score'] for s in pr + pt}
    for s in ranked + thin:
        s['prevScore'] = prev.get(s['theme'])
        s['scoreRise'] = None if s['prevScore'] is None else s['score'] - s['prevScore']
        s['isNew'] = bool(s['prevScore'] is not None and s['prevScore'] < ROT_WAS_WEAK
                          and s['score'] >= ROT_NOW and s['scoreRise'] >= ROT_RISE)
    ranked.sort(key=lambda s: (not s['isNew'], -s['score']))
    return ranked, thin
