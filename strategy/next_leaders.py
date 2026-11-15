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
from strategy.rs_correction import _index_peak_date, _pre_adr, calc_correction_rs, is_valid_rs_range

# 차기 주도주 배점 (100점). 2026-11-09 5년 검증 반영:
#  - 찐반등날 21EMA 위 여부는 이후 성과와 오히려 반대(위 −2.9%p vs 아래 −1.0%p, 60일 중앙값) → 제외
#  - 거래량 우위(상승일/하락일 거래량 = 추세추종 탭 거래량비)도 역효과(≥120% 통과 −2.6%p vs 탈락 −0.3%p) → 제외
#  - 52주 고점 근접(−20% 이내)이 가장 일관되게 효과 → 비중 확대
#  - 진짜 반등에서는 반등 세기(찐반등 전날~기준일 RS/ADR)가 하락장 RS보다 예측력이 높았다 → 40점
#  - 장기 추세(200일선 방향): 200일선이 20거래일 전보다 높으면(상승 중) 10, 아니면 0. 주가 위치(이격)는 안 본다.
#    2026-11-14 5년 검증(찐반등 약 70회, 상위 20, 이후 60일 지수 대비): 200일선 위 종목끼리도 상승 중 +0.5%p vs
#    하락 중 −6.1%p(이벤트 33/51, 3시장 같은 방향). 이격 단계 방식(위 10·0~−15% 5·그 밑 0) → 방향만 보기로 바꾸니
#    상위 20 중 약 10% 교체, 이벤트 41/67 개선(빠진 종목 60일 평균 −3.0%p vs 들어온 종목 +6.9%p).
#    책 추세 템플릿 8조건 중 나머지(150·200 정배열·52주 저점+30%·20일 범위·RS 90·RS 상승·과열)는 효과 없음/반대.
WEIGHTS = {
    'correction': {'덜 빠짐': 35, '고점 근접': 35, '저점 선행': 20, '장기 추세': 10},
    'rebound': {'찐반등 후 회복': 35, '덜 빠짐': 20, '고점 근접': 25, '양봉 렐볼': 10, '장기 추세': 10},
}
SMA200_SLOPE_DAYS = 20      # 200일선 방향 = 지금 200일선 vs 이 거래일 전 200일선


def sma200_gap(df: pd.DataFrame) -> float | None:
    """200일선 이격(%) = 종가 / 200일 단순이평 − 1. 이력이 200일 미만이면 None."""
    c = df['Close']
    if len(c) < 200:
        return None
    s = float(c.iloc[-200:].mean())
    return 100 * (float(c.iloc[-1]) / s - 1) if s > 0 else None


def below_sma(df: pd.DataFrame, n: int) -> bool:
    """종가 < n일 단순이평 — pivot_candle.pp_prefilter의 150·200일선 조건과 같은 정의(같은 rolling 계산).
    이력이 n일 미만(이평 없음)이면 False = 관문 통과 (pp_prefilter도 이평이 없으면 거르지 않는다)."""
    s = df['Close'].rolling(n).mean().iloc[-1]
    return bool(not pd.isna(s) and float(df['Close'].iloc[-1]) < float(s))


def sma200_rising(df: pd.DataFrame) -> bool | None:
    """200일선이 SMA200_SLOPE_DAYS 거래일 전보다 높은가. 이력이 부족하면 None."""
    c = df['Close']
    if len(c) < 200 + SMA200_SLOPE_DAYS:
        return None
    now = float(c.iloc[-200:].mean())
    before = float(c.iloc[-200 - SMA200_SLOPE_DAYS:-SMA200_SLOPE_DAYS].mean())
    return now > before


def trend_points(rising: bool | None) -> float:
    """장기 추세 점수 비율: 200일선 상승 중 1.0 · 하락(또는 이력 부족) 0."""
    return 1.0 if rising else 0.0
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
    - 수동 값 때문에 기산점이 찐반등일과 같거나 뒤가 되면 그 수동 값을 무시한다 (#29)
    'warning': 수동 값을 무시·조정했을 때 화면에 띄울 문구, 없으면 None
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
    warning = None

    if jjin is not None:
        jjin_d = _snap(idf, jjin)
        after = len(idf) - 1 - idf.index.get_loc(jjin_d)
        mode, label = 'rebound', (f'DAY{after + 2}' if after <= 5 else f'+{after}일')
    if peak is not None:
        peak_d = _snap(idf, peak)
        if jjin is None and jjin_d is not None and jjin_d <= peak_d:
            warning = (f"수동 기산점({peak_d:%m/%d})이 자동 찐반등일({jjin_d:%m/%d})과 같거나 뒤라 그 찐반등을 무시하고 "
                       "기산점부터 조정 국면('버틴 힘')으로 계산합니다.")
            jjin_d = None       # 수동 기산점이 자동 찐반등보다 뒤면 그 찐반등은 무효
        if jjin_d is None and mode != 'correction':
            # 찐반등 국면(DAY2~)이든 상승 추세든 찐반등이 없어졌으면 조정 국면 — 'rebound'로 남으면
            # 찐반등일 없는 찐반등 화면이 되어 배너가 None을 포맷하다 죽는다(#29)
            mode, label = 'correction', '수동 기산점'
    if jjin_d is not None and peak_d is None:
        # 찐반등일만 있고 자동 조정이 없을 때 — 찐반등일 이전 3개월 최고 종가를 기산점으로.
        # 찐반등일 당일은 뺀다: 상승 추세에선 그날이 최고 종가라 기산점 = 찐반등일 → 빈 구간(#29)
        win = idf[idf.index < jjin_d].tail(LOOKBACK_HIGH)
        peak_d = win['Close'].idxmax() if len(win) else jjin_d     # 이전 거래일이 없으면 아래 검증에서 무시
    if mode == 'correction' and peak_d is None:
        peak_d = idf.tail(LOOKBACK_HIGH)['Close'].idxmax()
    if peak_d is not None and (cs is None or (peak is not None and cs < peak_d)):
        cs = peak_d     # 자동 조정이 없거나 기산점을 뒤로 옮기면 ADR 기준 시점도 기산점으로
    # 최종 구간 검증 — 기산점이 찐반등일과 같거나 뒤면 하락국면 구간(calc_correction_rs)이 비어
    # 전 종목 '덜 빠짐'이 같은 점수로 고정된다(#29). 원인이 된 수동 값을 버리고 다시 계산한다.
    if (peak is not None or jjin is not None) and not is_valid_rs_range(peak_d, jjin_d):
        if peak is not None and jjin is not None:
            ignored, res = '수동 기산점', detect_phase(index_df, asof, None, jjin)   # 화면 규칙과 같게 기산점부터
        else:
            ignored, res = ('수동 기산점' if peak is not None else '수동 찐반등일'), detect_phase(index_df, asof)
        msg = (f'기산점({peak_d:%m/%d})이 찐반등일({jjin_d:%m/%d})과 같거나 뒤라 RS 구간이 비므로 {ignored}을 '
               '무시했습니다 — 기산점은 찐반등일보다 앞이어야 합니다.')
        res['warning'] = msg if not res.get('warning') else f"{msg} {res['warning']}"
        return res
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
            'manual': peak is not None or jjin is not None, 'warning': warning}


def _since_ret(df: pd.DataFrame, d) -> float | None:
    """d 종가 → 마지막 종가 수익률(%). d 봉이 없으면 직전 거래일 종가로 맞춘다 — 봉 하나 빠졌다고
    항목이 통째로 0점이 되지 않게(#41 M6). d 이전 봉이 아예 없으면(그 뒤 상장) None."""
    if d is None:
        return None
    before = df[df.index <= pd.Timestamp(d)]
    if before.empty:
        return None
    return 100 * (float(df['Close'].iloc[-1]) / float(before['Close'].iloc[-1]) - 1)


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
        r['sma200Rising'] = sma200_rising(df)
        s =_since_ret(df, phase.get('jjinBase') or phase['jjinDate'])    # 찐반등 전날 종가부터
        r['sinceJjinExcess'] = None if s is None or phase['indexSinceJjin'] is None else s - phase['indexSinceJjin']
        adr = _pre_adr(df, phase['correctionStart'])      # 기산점 쪽과 같은 ADR(조정 시작 전 20일)
        r['sinceJjinExcessAdr'] = (None if r['sinceJjinExcess'] is None or not adr
                                   else r['sinceJjinExcess'] / adr)

    def pct(key):
        return ss.percentile_rank({r['ticker']: r.get(key) for r in pool})

    # 지수 대비는 ADR 보정값만 점수에 쓴다 — 변동성 큰 종목이 %p만으로 상위를 차지하는 노이즈 제거
    p = {k: pct(k) for k in ('sincePeakExcessAdr', 'offHigh', 'leadDays', 'volRatio', 'sinceJjinExcessAdr')}

    for r in pool:
        t = r['ticker']
        # 백분위 None = 그 종목 값을 계산할 수 없음(기산점·찐반등 전 상장, 조정 전 ADR 없음 등) — 0점으로 두면
        # 조용히 순위가 밀리거나(GAP), 0.0이 실제 값처럼 섞인다(IPO). 그 항목만 빼고 남은 배점으로 재정규화(#41 M6).
        val = {'덜 빠짐': p['sincePeakExcessAdr'][t], '찐반등 후 회복': p['sinceJjinExcessAdr'][t],
               '고점 근접': p['offHigh'][t], '저점 선행': p['leadDays'][t],
               '양봉 렐볼': float(r['hasBullishRvol15InLast5']), '장기 추세': trend_points(r.get('sma200Rising'))}
        parts = {k: None if val[k] is None else w * val[k] for k, w in WEIGHTS[scoring].items()}
        used = sum(w for k, w in WEIGHTS[scoring].items() if parts[k] is not None)
        r['nextParts'] = parts
        r['nextScore'] = 100 * float(sum(v for v in parts.values() if v is not None)) / used if used else 0.0
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
#   장기 추세(200일선 방향) 10 — 차기 주도주와 같은 정의
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
            # 급부상 종목 탈락 규칙(hot_stock_pool) — 50일선 + PP 기준봉 조건인 150·200일선(pp_prefilter와 같은 정의)
            'belowSma50': below_sma(df, 50), 'belowSma150': below_sma(df, 150), 'belowSma200': below_sma(df, 200),
            'adrNow': adr_now,
            'ticker': t, 'primaryTheme': theme_of[t], 'status': 'FULL',
            'rotRs5': rs_now, 'rotRsPrev5': rs_prev, 'rotAccel': rs_now - rs_prev,
            'volRatio': vol_ratio, 'offHigh': 100 * (float(df['Close'].iloc[-1]) / hi - 1),
            'gap200': sma200_gap(df), 'sma200Rising': sma200_rising(df),
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
               '장기 추세': trend_points(x['sma200Rising'])}
        parts = {k: v * val[k] for k, v in ROT_WEIGHTS.items()}
        x['rotParts'], x['rotScore'] = parts, float(sum(parts.values()))
        pv = {'고점 근접': 1.0 if x['offHigh'] >= PP_NEAR_HIGH else 0.0,
              '10EMA 유지': x['ema10DaysOfLast5'] / 5, '정배열': float(x['aligned']),
              '양봉 렐볼': float(x['hasBullishRvol15InLast5']),
              '1주 RS/ADR': g('rotRs5', t)}
        pparts = {k: v * pv[k] for k, v in PP_WEIGHTS.items()}
        x['ppParts'], x['ppScore'] = pparts, float(sum(pparts.values()))
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
HOT_RVOL = ss.RVOL_BULL     # 렐볼 양봉 정의는 sector_strength.bullish_rvol 하나 (#41 M9)
HOT_GAP = 10            # 이 기간 안에 다시 켜진 건 같은 🔥로 본다 (50% 근처 깜빡임)
# 섹터 정배열 종목 비율이 이 미만이면 🔥여도 제외 — 2026-11-11 검증(🔥 섹터 1,005개): 20% 미만(35%)은 30일 안
# 기준봉 나온 섹터 26%·종목당 5.3%, 20~50% 43~46%·10%, 50%↑ 53~54%·13~14%. 200일선 위 비율·렐볼 강도는 효과 없음.
HOT_MIN_ALIGNED = 0.2


def fire_starts(on, gap: int = HOT_GAP) -> list:
    """🔥 시작 위치 — 켜졌는데 직전 gap거래일 동안 한 번도 안 켜졌던 날 (검증과 같은 정의 — 깜빡임은 한 번으로).
    앞쪽 gap일은 '그 전부터 켜져 있었나'를 알 수 없어 시작으로 치지 않는다."""
    on = np.asarray(on, dtype=bool)
    return [i for i in range(gap, len(on)) if on[i] and not on[i - gap:i].any()]


def hot_groups(bars: dict, theme_of: dict, asof) -> dict:
    """{테마: [티커…]} — 🔥 판정(hot_sectors)과 단독 렐볼 판정(hot_stock_pool의 sizes)이 함께 쓰는 섹터 구성.
    섹터 크기의 단일 출처(#41 M7): 분류된(미분류 아님) 종목 중 기준일 이전 봉이 하나라도 있는 종목.
    이력이 짧은 종목도 넣는다 — 빼면 두 판정이 서로 다른 크기를 써서 어느 쪽에도 안 잡히는 종목이 생겼다."""
    asof = pd.Timestamp(asof)
    out: dict = {}
    for t, df in bars.items():
        th = theme_of.get(t)
        if th in (None, 'UNCLASSIFIED') or not (df.index <= asof).any():
            continue
        out.setdefault(th, []).append(t)
    return out


def hot_sectors(bars: dict, theme_of: dict, asof, lookback: int = HOT_LOOKBACK,
                share: float = HOT_SHARE, min_n: int = MIN_SECTOR_N, min_aligned: float | None = None) -> dict:
    """{테마: {'now': 오늘 🔥?, 'share': 오늘 렐볼 양봉 비율, 'n': 섹터 크기, 'fires': [창 안 🔥 시작일…],
    'last'·'since': 지금 이어지는 🔥 구간의 시작일 = 편입일, 'startedBefore': 시작일 모름(이력 처음부터 이어짐),
    'peakShare', 'tickers': 최근 5일 렐볼 양봉 종목}} — 최근 lookback 거래일 안에 한 번이라도 🔥였던 섹터만.
    렐볼 양봉 = sector_strength.bullish_rvol. 비율의 분모는 섹터 크기(hot_groups)로 고정하고, 그날 봉이 없거나
    이력이 짧아 판정할 수 없는 종목은 '렐볼 아님'으로 센다(#41 M7 — 한 종목 결측으로 🔥가 꺼지지 않게)."""
    asof = pd.Timestamp(asof)
    flags, aligned = {}, {}
    groups = {th: ts for th, ts in hot_groups(bars, theme_of, asof).items() if len(ts) >= min_n}
    for ts in groups.values():
        for t in ts:
            full = bars[t][bars[t].index <= asof]
            # 편입일을 창 길이에 잘리지 않게 정하려면 창 밖 이력도 필요하다 — 스냅샷 전체(약 240봉)로 계산(#30).
            # 직전 20일 평균이 없는 첫 20봉은 '안 켜짐'이 아니라 판정 불가(NaN) — 섹터 판정 시작일을 정할 때 쓴다.
            bull = ss.bullish_rvol(full).astype(float)
            bull.iloc[:20] = np.nan
            flags[t] = bull.rolling(5, min_periods=1).max()
            c = full['Close'].tail(250)
            s50 = c.rolling(50).mean().iloc[-1]
            aligned[t] = bool(not pd.isna(s50) and c.ewm(span=10, adjust=False).mean().iloc[-1]
                              > c.ewm(span=21, adjust=False).mean().iloc[-1] > s50)
    out = {}
    for th, ts in groups.items():
        # 섹터 품질 — 정배열(10EMA>21EMA>50SMA) 종목 비율이 낮으면 🔥여도 뺀다 (바닥권 종목들이 거래량만 터진 경우)
        al_share = sum(aligned[t] for t in ts) / len(ts)
        if al_share < (HOT_MIN_ALIGNED if min_aligned is None else min_aligned):
            continue
        f = pd.DataFrame({t: flags[t] for t in ts})
        n = f.notna().sum(axis=1)
        # 섹터 판정이 가능한 첫날부터 (그 앞은 '모름'). 이력 짧은 종목이 끝내 판정 불가로 남아도 섹터 판정은
        # 시작돼야 하므로 기준은 min_n과 '판정 가능 종목 수의 최대' 중 작은 쪽
        known = (n >= min(min_n, int(n.max()))).to_numpy() if len(n) and n.max() > 0 else np.zeros(0, dtype=bool)
        if not known.any():
            continue
        first = int(np.argmax(known))
        f = f.iloc[first:]
        sh = f.fillna(0).sum(axis=1) / len(ts)      # 결측 = 렐볼 아님, 분모 = 섹터 크기 고정
        on = (sh >= share).to_numpy()
        w0 = max(len(on) - lookback, 0)          # 창 = 최근 lookback거래일 (이력 표시·지금 🔥·최고 비율)
        if not on[w0:].any():
            continue
        starts = fire_starts(on)
        fires = [f.index[i] for i in starts if i >= w0]
        # 편입일 = 지금 이어지는 🔥 구간의 시작(= 마지막 🔥 시작). 창 밖이어도 HOT_GAP보다 긴 공백이 나올 때까지
        # 이력을 거슬러 찾는다 — 창 안 첫 켜짐을 쓰면 창이 밀릴 때마다 편입일이 늦어져 탈락 기준 고점이
        # 낮아지고, 3 ADR 하락으로 빠진 종목이 가격 회복 없이 돌아왔다(#30)
        last = f.index[starts[-1]] if starts else None      # None = 이력 처음부터 이어진 🔥 (시작일 모름)
        since = last if last is not None else f.index[int(np.argmax(on))]   # 모르면 알 수 있는 첫 켜짐
        out[th] = {'now': bool(on[-1]), 'share': float(sh.iloc[-1]), 'n': len(ts),
                   'fires': fires, 'last': last, 'peakShare': float(sh.iloc[w0:].max()),
                   'since': since, 'startedBefore': last is None, 'alignedShare': al_share,
                   'daysAgo': (len(on) - 1 - starts[-1]) if starts else lookback,
                   'tickers': [t for t in ts if f[t].iloc[-1] == 1]}
    return out


def bull_since(bars: dict, asof) -> dict:
    """{티커: 최근 5일 안 첫 렐볼 양봉 날} — 지금 '최근 5일 렐볼 양봉'인 종목만. 단독 렐볼 종목의 편입일."""
    asof = pd.Timestamp(asof)
    out = {}
    for t, df in bars.items():
        d = df[df.index <= asof].tail(26)
        if len(d) < 21:
            continue
        bull = ss.bullish_rvol(d).tail(5)
        if bull.any():
            out[t] = bull[bull].index[0]
    return out


def hot_stock_pool(rot_stocks: list, hot: dict, sizes: dict, bull_since: dict, bars: dict, asof) -> dict:
    """🔥 급부상 종목 — 후보와 탈락·관문 규칙 (UI는 토글 필터·표시만, #41 M9).

    후보: 🔥 섹터(hot) 소속 종목 + 단독 렐볼(섹터 종목 수 sizes가 MIN_SECTOR_N 미만이고 최근 5일 렐볼 양봉).
    관문(순서대로): ADR 6% 이상(PP_MIN_ADR, 10EMA 탭 대상) → 편입 후 자기 렐볼 양봉 있음(종목 자신도 핫해야)
      → 편입 5거래일 전부터의 종가 고점 대비 하락이 PP_DROP_ADR(3 ADR) 미만 → 종가가 50·150·200일선 위
      (150·200은 PP 기준봉 조건 = pp_prefilter, '50점 미만 보기'에서도 뺀다).
    편입일 = 🔥 섹터면 지금 이어지는 🔥 구간의 시작(hot 'since'), 단독 렐볼이면 bull_since.
    🔥는 최근 5일 렐볼 양봉으로 켜지므로 종목의 움직임은 편입일보다 최대 5거래일 앞설 수 있다 — 그날부터 잰다.
    반환 {'rows': 관문 통과 행(PP 가능성 점수 내림차순, 입력 행의 사본)}. 행에 붙는 값:
      'solo', 'entry'·'entryUnknown'(편입일·시작일 모름), 'isNew'(오늘 편입), 'ddAdr', 'ownBull',
      'weak'(PP_MIN_SCORE 미만 — 기본 숨김)."""
    asof = pd.Timestamp(asof)
    rows = []
    for r in rot_stocks:
        th = r['primaryTheme']
        solo = th not in hot and sizes.get(th, 0) < MIN_SECTOR_N and bool(r['hasBullishRvol15InLast5'])
        if th not in hot and not solo:
            continue
        h = hot.get(th)
        entry, unknown = (h['since'], h['startedBefore']) if h else (bull_since.get(r['ticker']), False)
        rows.append({**r, 'solo': solo, 'entry': entry, 'entryUnknown': unknown,
                     'isNew': entry is not None and not unknown and pd.Timestamp(entry) == asof})
    rows = [r for r in rows if r['adrNow'] >= PP_MIN_ADR]      # 미만은 10EMA 탭 판정 대상이 아님
    for r in rows:
        r['ddAdr'], r['ownBull'] = _since_entry(r, bars.get(r['ticker']), asof)
    # 종목 자신도 핫해야 한다 — 편입 후 자기 렐볼 양봉이 한 번도 없으면 섹터만 뜨겁고 종목은 안 움직인 것.
    # 검증(🔥 뒤 0·10·20일): 없음 4~36%, 이후 30일 기준봉 17~21% vs 있음 21~25%
    rows = [r for r in rows if r['ownBull']]
    # 탈락 — 편입 후 종가 고점에서 3 ADR 이상 하락(이후 30일 기준봉 3.6~3.9% vs 평균 16~17%), 종가가 50일선 아래
    # (8.6~9.8%), 150·200일선 아래(PP 기준봉 조건). 매일 다시 계산 — 가격이 회복하면 다음 날 목록에 돌아온다
    rows = [r for r in rows if r['ddAdr'] > -PP_DROP_ADR and not r['belowSma50']
            and not r['belowSma150'] and not r['belowSma200']]
    for r in rows:
        r['weak'] = r['ppScore'] < PP_MIN_SCORE
    rows.sort(key=lambda r: -r['ppScore'])
    return {'rows': rows}


def _since_entry(r: dict, df: pd.DataFrame | None, asof: pd.Timestamp) -> tuple[float, bool]:
    """(편입 5거래일 전부터의 종가 고점 대비 하락 ADR 배수, 그 사이 자기 렐볼 양봉이 있었나).
    편입일을 모르거나 시세가 없으면 (0, 최근 5일 렐볼 양봉) — 예전 UI 규칙 그대로."""
    since = r['entry']
    fallback = 0.0, bool(r['hasBullishRvol15InLast5'])
    if since is None or df is None or not r['adrNow']:
        return fallback
    df = df[df.index <= asof]
    pos = df.index.searchsorted(pd.Timestamp(since))
    w = df.iloc[max(0, pos - 5):]
    if w.empty:
        return fallback
    dd = 100 * (float(w['Close'].iloc[-1]) / float(w['Close'].max()) - 1) / r['adrNow']
    return dd, bool(ss.bullish_rvol(df).iloc[max(0, pos - 5):].any())
