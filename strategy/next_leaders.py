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

# 차기 주도주 배점 (100점). 2026-11-09 5년 검증 반영:
#  - 찐반등날 21EMA 위 여부는 이후 성과와 오히려 반대(위 −2.9%p vs 아래 −1.0%p, 60일 중앙값) → 제외
#  - 거래량 우위(상승일/하락일 거래량 = 추세추종 탭 거래량비)도 역효과(≥120% 통과 −2.6%p vs 탈락 −0.3%p) → 제외
#  - 52주 고점 근접(−20% 이내)이 가장 일관되게 효과 → 비중 확대
#  - 진짜 반등에서는 반등 세기(찐반등 전날~기준일 RS/ADR)가 하락장 RS보다 예측력이 높았다 → 40점
#  - 장기 추세(200일선 이격): 반등 세기 상위 종목 중 200일선 위에서 대박(+30%p)이 가장 많았고,
#    200일선 아래 −15~−30% 이격 구간이 가장 약했다(2026-11-10 검증) → 위 10 · 아래 0~−15% 5 · 그 밑 0
WEIGHTS = {
    'correction': {'덜 빠짐': 35, '고점 근접': 35, '저점 선행': 20, '장기 추세': 10},
    'rebound': {'찐반등 후 회복': 35, '덜 빠짐': 20, '고점 근접': 25, '양봉 렐볼': 10, '장기 추세': 10},
}
TREND_NEAR_PCT = -15.0      # 200일선 아래라도 이 이격 안이면 장기 추세 점수 절반


def sma200_gap(df: pd.DataFrame) -> float | None:
    """200일선 이격(%) = 종가 / 200일 단순이평 − 1. 이력이 200일 미만이면 None."""
    c = df['Close']
    if len(c) < 200:
        return None
    s = float(c.iloc[-200:].mean())
    return 100 * (float(c.iloc[-1]) / s - 1) if s > 0 else None


def trend_points(gap: float | None) -> float:
    """장기 추세 점수 비율: 200일선 위 1.0 · 아래 0~−15% 이격 0.5 · 그 밑(또는 이력 부족) 0."""
    if gap is None:
        return 0.0
    return 1.0 if gap >= 0 else 0.5 if gap >= TREND_NEAR_PCT else 0.0
MIN_SECTOR_N = 3
STRONG_SCORE = 70
LOOKBACK_HIGH = 63      # 지수 기산점 보완용 창 (약 3개월)
LOOKBACK_52W = 252      # 종목 고점 근접 = 52주 고가 대비 — 추세추종 탭과 같은 기준.
                        # 5년 검증: 찐반등날 52주 고점 −20% 이내가 이후 성과 가장 좋았다


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
    # 종목 순위는 테마와 무관하게 분류된 모든 종목 (추세추종 탭과 같은 대상). 섹터 순위만 메인 테마로 거른다.
    pool = [r for r in rows if r['status'] == 'FULL' and theme_of.get(r['ticker']) not in (None, 'UNCLASSIFIED')]
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
        hi = float(df['High'].iloc[-LOOKBACK_52W:].max())
        r['offHigh'] = 100 * (float(df['Close'].iloc[-1]) / hi - 1)
        r['gap200'] = sma200_gap(df)
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
        val = {'덜 빠짐': held, '찐반등 후 회복': g('sinceJjinExcessAdr', t), '고점 근접': g('offHigh', t),
               '저점 선행': g('leadDays', t), '양봉 렐볼': float(r['hasBullishRvol15InLast5']),
               '장기 추세': trend_points(r.get('gap200'))}
        parts = {k: v * val[k] for k, v in WEIGHTS[scoring].items()}
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
# 차기 주도와 같은 재료(지수 대비 RS/ADR·52주 고점·양봉 렐볼)를 쓰되, 기준 구간이 찐반등이 아니라
# 최근 1~2주 롤링이다. 덜 빠짐은 없다(순환매 섹터는 보통 조정 때 약했던 곳).
# 2026-11-09 5년 검증(상승 추세 구간 297일, 이후 20일 지수 대비):
#   효과 있음 — 52주 고점 근접(상위20% +0.1%p vs 하위 −2.0%p), 10EMA 5일 유지, 양봉 렐볼
#   효과 없음 — 1주 RS/ADR, RS 가속(이번 주−지난주) → RS 가속 제외, 1주 RS/ADR은 순환매 정의라 20만 유지
#   역효과   — 거래량 우위 → 제외 (차기 주도주·추세추종 탭과 동일)
#   21EMA 위 — 거의 없음 + 10EMA와 중복 → 제외 (차기 주도주와 동일)
#   장기 추세(200일선 이격) 10 — 차기 주도주와 같은 정의
ROT_WEIGHTS = {'고점 근접': 30, '10EMA 유지': 25, '1주 RS/ADR': 15, '양봉 렐볼': 20, '장기 추세': 10}
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
        if r['status'] != 'FULL' or theme_of.get(t) in (None, 'UNCLASSIFIED') or t not in bars:
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
        hi = float(df['High'].iloc[-LOOKBACK_52W:].max())
        c = df['Close']
        e10 = float(c.ewm(span=10, adjust=False).mean().iloc[-1])
        e21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
        s50 = c.rolling(50).mean().iloc[-1]
        last20 = df.tail(20)
        adr_now = float(((last20['High'] - last20['Low']) / last20['Close'] * 100).mean())
        pool.append({
            'aligned': bool(not pd.isna(s50) and e10 > e21 > float(s50)),     # 10EMA > 21EMA > 50SMA (PP 기준봉 조건)
            'belowSma50': bool(not pd.isna(s50) and float(c.iloc[-1]) < float(s50)),   # 급부상 종목 탈락 규칙
            'adrNow': adr_now,
            'ticker': t, 'primaryTheme': theme_of[t], 'status': 'FULL',
            'rotRs5': rs_now, 'rotRsPrev5': rs_prev, 'rotAccel': rs_now - rs_prev,
            'volRatio': vol_ratio, 'offHigh': 100 * (float(df['Close'].iloc[-1]) / hi - 1),
            'gap200': sma200_gap(df),
            'aboveEma10': r['aboveEma10'], 'aboveEma21': r['aboveEma21'],
            'ema10DaysOfLast5': r['ema10DaysOfLast5'],
            'hasBullishRvol15InLast5': r['hasBullishRvol15InLast5'],
        })
    if not pool:
        return []
    p = {k: ss.percentile_rank({x['ticker']: x[k] for x in pool}) for k in ('rotRs5', 'offHigh')}
    g = lambda k, t: p[k][t] or 0.0
    for x in pool:
        t = x['ticker']
        val = {'고점 근접': g('offHigh', t), '10EMA 유지': x['ema10DaysOfLast5'] / 5,
               '1주 RS/ADR': g('rotRs5', t), '양봉 렐볼': float(x['hasBullishRvol15InLast5']),
               '장기 추세': trend_points(x['gap200'])}
        parts = {k: v * val[k] for k, v in ROT_WEIGHTS.items()}
        x['rotParts'], x['rotScore'] = parts, float(sum(parts.values()))
        pv = {'고점 근접': 1.0 if x['offHigh'] >= PP_NEAR_HIGH else 0.0,
              '10EMA 유지': x['ema10DaysOfLast5'] / 5, '정배열': float(x['aligned']),
              '양봉 렐볼': float(x['hasBullishRvol15InLast5']),
              '1주 RS/ADR': g('rotRs5', t)}
        pparts = {k: v * pv[k] for k, v in PP_WEIGHTS.items()}
        x['ppParts'], x['ppScore'] = pparts, float(sum(pparts.values()))
        x['ppEligible'] = x['adrNow'] >= PP_MIN_ADR
    return pool


# ── 🔥 급부상 종목 'PP 가능성 점수' — 기준봉을 만들어낼 가능성 ───────────────────
# 2026-11-11 5년 검증(🔥 1,011회 × 섹터 종목 7,512, 30일 안 PP 기준봉 비율 평균 10%):
#   ADR 6%↑ 17~18% vs 4%↓ 3% (10EMA 탭 대상 자체가 6%↑) · 정배열 15% vs 7% · 200일선 +50%↑ 17% vs −15%↓ 5%
#   10EMA 5/5 14% vs 0/5 4% · 52주 고점 −20% 이내 14% vs 밖 8% (−10~0과 −20~−10 차이 없음 → 이내면 만점)
#   1주 RS/ADR 상위 12.5% vs 하위 7% · 양봉 렐볼 11% vs 8% (🔥 섹터라 변별력 약함)
# 이후 수익 예측력은 약함 — '기준봉에 가까운 모양' 순서일 뿐. ADR 6% 미만은 점수와 별개로 'PP 대상 아님' 표시.
# 2026-11-11 장기 추세(200일선 이격) 삭제 — 200일선 아래를 급부상 종목에서 빼고 나니(관문) 위 종목끼리는 이격 단계별
# 기준봉 비율에 일관된 차이가 없었고, 빼고 재배분해도 상위 25% 기준봉 24.7% → 24.4%로 같았다.
PP_WEIGHTS = {'고점 근접': 30, '10EMA 유지': 25, '정배열': 25, '양봉 렐볼': 10, '1주 RS/ADR': 10}
PP_NEAR_HIGH = -20.0
PP_MIN_ADR = 6.0        # 미만은 10EMA 탭 판정 대상이 아니라 급부상 종목에서 제외
PP_MIN_SCORE = 50       # 이상만 표시 — 검증: 50↑ 30일 안 기준봉 24~30%, 30~40 14%, 30↓ 9% (ADR 6%↑ 평균 17%)
PP_DROP_ADR = 3.0       # 편입 후 종가 고점에서 이 ADR 배수 이상 밀리면 탈락 — 검증: 이후 기준봉 3.6~3.9%



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


# ── 🔥 돈 몰린 섹터 — 갑자기 핫해지는 섹터 감지 ───────────────────
# 2026-11-11 5년 검증(섹터 64개, 급부상 467회 = 이후 20일 섹터 초과수익 ≥10%p 시작일):
#   '섹터 종목 절반 이상이 최근 5일 안에 렐볼(1.5배) 양봉' → 급부상의 61%를 시작 1~2일 뒤에 잡음 (가장 빠르고 많이)
#   순환매 점수 ≥60·🆕는 20~29%를 5~6일 늦게 잡음. 단, 어떤 신호든 '신호 뒤 계속 오를지'는 예측 못 함 → 알림 용도.
HOT_SHARE = 0.5         # 섹터 종목 중 이 비율 이상이 최근 5일 렐볼 양봉이면 🔥
HOT_LOOKBACK = 30       # 🔥 이력을 남기는 기간(거래일, 약 6주) — 돈이 몰리고 PP 셋업이 나오기까지 몇 주 걸린다
HOT_RVOL = 1.5
HOT_GAP = 10            # 이 기간 안에 다시 켜진 건 같은 🔥로 본다 (50% 근처 깜빡임)


def hot_sectors(bars: dict, theme_of: dict, asof, lookback: int = HOT_LOOKBACK,
                share: float = HOT_SHARE, min_n: int = MIN_SECTOR_N) -> dict:
    """{테마: {'now': 오늘 🔥?, 'share': 오늘 렐볼 양봉 비율, 'n', 'fires': [🔥 시작일…], 'last': 마지막 🔥 시작일,
    'peakShare', 'tickers': 최근 5일 렐볼 양봉 종목}} — 최근 lookback 거래일 안에 한 번이라도 🔥였던 섹터만.
    렐볼 양봉 = 종가 > 시가 & 거래량 ≥ 직전 20일 평균 × 1.5 (sector_strength와 같은 정의)."""
    asof = pd.Timestamp(asof)
    flags, groups = {}, {}
    for t, df in bars.items():
        th = theme_of.get(t)
        if th in (None, 'UNCLASSIFIED'):
            continue
        d = df[df.index <= asof].tail(lookback + HOT_GAP + 30)
        if len(d) < 30:
            continue
        avgv = d['Volume'].shift(1).rolling(20).mean()
        bull = (d['Close'] > d['Open']) & (d['Volume'] >= HOT_RVOL * avgv)
        flags[t] = bull.astype(float).rolling(5, min_periods=1).max().fillna(0).astype(bool)
        groups.setdefault(th, []).append(t)
    out = {}
    for th, ts in groups.items():
        if len(ts) < min_n:
            continue
        f = pd.DataFrame({t: flags[t] for t in ts})
        f = f[f.index <= asof].tail(lookback + HOT_GAP)     # 창 앞 HOT_GAP일은 '그 전부터 켜져 있었나' 판단용
        n = f.notna().sum(axis=1)
        sh = (f.astype(float).fillna(0).sum(axis=1) / n.where(n >= min_n)).fillna(0)
        on = (sh >= share).to_numpy()
        # 🔥 시작 = 켜졌는데 직전 HOT_GAP거래일 동안 한 번도 안 켜졌던 날 (검증과 같은 정의 — 깜빡임은 한 번으로)
        starts = [i for i in range(HOT_GAP, len(on)) if on[i] and not on[i - HOT_GAP:i].any()]
        win_on = on[HOT_GAP:]
        if not win_on.any():
            continue
        fires = [f.index[i] for i in starts]
        last = fires[-1] if fires else None      # None = 창(약 6주)보다 전부터 이어진 🔥
        # 편입일 = 창 안에서 처음 켜진 날. 그날이 🔥 시작이 아니면(창 앞부터 이어짐) 시작일을 모른다
        first_on = HOT_GAP + int(np.argmax(win_on))
        since = f.index[first_on]
        started_before = since not in fires
        out[th] = {'now': bool(on[-1]), 'share': float(sh.iloc[-1]), 'n': int(n.iloc[-1]),
                   'fires': fires, 'last': last, 'peakShare': float(sh.iloc[HOT_GAP:].max()),
                   'since': since, 'startedBefore': started_before,
                   'daysAgo': (len(on) - 1 - f.index.get_loc(last)) if last is not None else lookback,
                   'tickers': [t for t in ts if f[t].astype(float).iloc[-1] == 1]}
    return out


def bull_since(bars: dict, asof) -> dict:
    """{티커: 최근 5일 안 첫 렐볼 양봉 날} — 지금 '최근 5일 렐볼 양봉'인 종목만. 단독 렐볼 종목의 편입일."""
    asof = pd.Timestamp(asof)
    out = {}
    for t, df in bars.items():
        d = df[df.index <= asof].tail(26)
        if len(d) < 21:
            continue
        avgv = d['Volume'].shift(1).rolling(20).mean()
        bull = ((d['Close'] > d['Open']) & (d['Volume'] >= HOT_RVOL * avgv)).tail(5)
        if bull.any():
            out[t] = bull[bull].index[0]
    return out
