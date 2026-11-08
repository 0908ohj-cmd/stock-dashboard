"""미국 기술·혁신 성장주 주도섹터 엔진 (마스터 명세 2026-11-08 4~8장).

입력은 일봉 DataFrame(Open/High/Low/Close/Volume, 분할 보정)과 종목별 주력 테마 코드.
모든 계산은 asof 이하 일봉만 쓴다 (point-in-time). 데이터가 부족하면 값을 만들지 않고
status로 이유를 남긴다 — 결측은 약세(0점)가 아니다.
Streamlit 무의존.
"""
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BENCHMARKS = ('QQQ', 'IWM')
MIN_BARS = 60            # EMA21 초기값 영향 < 1% (0.913^60). 120 미만은 shortHistory 경고
RECOMMENDED_BARS = 120
SPLIT_JUMP = 0.6         # 하루 ±60% 이상 변동 → SPLIT_CHECK 경고 (분할 미보정 의심)
MIN_SECTOR_N = 5
MIN_COVERAGE = 0.8
LIQUIDITY_SHARES = 1_000_000

SCORE_WEIGHTS = {        # 명세 7장 — 합계 100
    'rs5': 10, 'nrs5': 10, 'rs21': 10, 'nrs21': 10,
    'above10': 20, 'persist': 15, 'above21': 10, 'rvolBreadth': 10, 'participation': 5,
}


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def ret_n(close: pd.Series, n: int) -> float | None:
    """오늘 종가 / n거래일 전 종가 − 1 (%)."""
    if len(close) < n + 1:
        return None
    base = float(close.iloc[-1 - n])
    return None if base <= 0 else 100 * (float(close.iloc[-1]) / base - 1)


def adr20_pct(df: pd.DataFrame) -> float | None:
    """최근 20개 일봉 mean(100*(High−Low)/Low)."""
    tail = df.iloc[-20:]
    if len(tail) < 20 or (tail['Low'] <= 0).any():
        return None
    val = float((100 * (tail['High'] - tail['Low']) / tail['Low']).mean())
    return val if val > 0 else None


def rvol20_series(volume: pd.Series) -> pd.Series:
    """RVOL20_t = V_t / mean(V_{t-20..t-1}) — 당일 거래량은 분모에서 제외."""
    prior = volume.shift(1).rolling(20).mean()
    return volume / prior.where(prior > 0)


def percentile_rank(values: dict) -> dict:
    """{key: 0~1}. 동점은 평균 순위, 최하위 0·최상위 1. 유효값 2개 미만이면 전부 None."""
    valid = {k: v for k, v in values.items() if v is not None and not math.isnan(v)}
    out = {k: None for k in values}
    if len(valid) < 2:
        return out
    ranks = pd.Series(valid).rank(method='average')
    n = len(valid)
    for k, r in ranks.items():
        out[k] = float((r - 1) / (n - 1))
    return out


def _median(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None


def _ratio(flags):
    flags = [f for f in flags if f is not None]
    return sum(bool(f) for f in flags) / len(flags) if flags else None


# ── 종목 지표 ─────────────────────────────────────────────

@dataclass
class Bench:
    asof: pd.Timestamp
    ret5: dict          # {벤치마크명: %} — 순서대로 B1, B2
    ret21: dict
    dates: pd.DatetimeIndex   # 벤치마크 거래일 (누락일 판정 기준)
    names: tuple = BENCHMARKS

    @property
    def blend5(self):
        return sum(self.ret5[n] for n in self.names) / len(self.names)

    @property
    def blend21(self):
        return sum(self.ret21[n] for n in self.names) / len(self.names)


def build_bench(bars: dict, asof, names: tuple = BENCHMARKS) -> Bench:
    """벤치마크 2개(US: QQQ·IWM, KR: KOSPI·KOSDAQ)가 asof 당일 종가를 가져야 한다.
    아니면 RS를 계산할 수 없다."""
    asof = pd.Timestamp(asof)
    r5, r21, dates = {}, {}, None
    for b in names:
        df = bars.get(b)
        if df is None or df.empty:
            raise ValueError(f'{b} 일봉 없음 — RS 계산 불가')
        df = df[df.index <= asof]
        if df.empty or df.index[-1] != asof:
            last = df.index[-1].date() if not df.empty else None
            raise ValueError(f'{b} {asof.date()} 종가 없음(마지막 {last}) — RS 계산 불가')
        r5[b], r21[b] = ret_n(df['Close'], 5), ret_n(df['Close'], 21)
        if r5[b] is None or r21[b] is None:
            raise ValueError(f'{b} 이력 부족 — RS 계산 불가')
        dates = df.index if dates is None else dates.intersection(df.index)
    return Bench(asof=asof, ret5=r5, ret21=r21, dates=dates, names=tuple(names))


def stock_metrics(ticker: str, df: pd.DataFrame | None, bench: Bench,
                  liquidity_shares: int | None = LIQUIDITY_SHARES,
                  liquidity_value: float | None = None) -> dict:
    """liquidity_shares: 20일 평균 거래량 기준(US), liquidity_value: 20일 평균 거래대금 기준(KR)."""
    out = {'ticker': ticker, 'asOf': bench.asof.strftime('%Y-%m-%d'), 'flags': []}
    if df is None or df.empty:
        return {**out, 'status': 'MISSING', 'missingReason': '일봉 없음 (수집 실패 또는 미수집)'}
    df = df[df.index <= bench.asof].sort_index()
    df = df[~df.index.duplicated(keep='last')]
    out['bars'] = len(df)
    out['lastDate'] = df.index[-1].strftime('%Y-%m-%d') if len(df) else None
    if df.empty:
        return {**out, 'status': 'MISSING', 'missingReason': f'{out["asOf"]} 이전 일봉 없음'}
    if df.index[-1] != bench.asof:
        return {**out, 'status': 'STALE', 'missingReason': f'마지막 일봉 {out["lastDate"]} — 기준일 종가 없음'}
    if len(df) < MIN_BARS:
        return {**out, 'status': 'SHORT_HISTORY', 'missingReason': f'일봉 {len(df)}개 < {MIN_BARS}'}

    recent = df.iloc[-25:]
    bad = recent[['Open', 'High', 'Low', 'Close']].isna().any(axis=1) | \
        (recent[['Open', 'High', 'Low', 'Close']] <= 0).any(axis=1) | \
        (recent['High'] < recent['Low']) | recent['Volume'].isna() | (recent['Volume'] < 0)
    if bad.any():
        return {**out, 'status': 'INVALID_DATA',
                'missingReason': f'이상 일봉 {int(bad.sum())}개 (최근 25일): '
                                 + ', '.join(d.strftime('%m-%d') for d in recent.index[bad][:3])}
    # 벤치마크 거래일 대비 누락 — 건너뛰면 5D/21D 간격이 조용히 늘어난다
    need = bench.dates[-22:]
    gaps = need.difference(df.index)
    if len(gaps):
        return {**out, 'status': 'INVALID_DATA',
                'missingReason': '최근 22거래일 중 누락: ' + ', '.join(d.strftime('%m-%d') for d in gaps[:3])}

    if len(df) < RECOMMENDED_BARS:
        out['flags'].append('SHORT_HISTORY_WARN')
    jumps = df['Close'].pct_change().iloc[-60:].abs()
    if (jumps > SPLIT_JUMP).any():
        out['flags'].append('SPLIT_CHECK')
    if (recent['Volume'] == 0).any():
        out['flags'].append('ZERO_VOLUME')

    close, vol = df['Close'], df['Volume']
    adr = adr20_pct(df)
    if adr is None:
        return {**out, 'status': 'INVALID_DATA', 'missingReason': 'ADR20 계산 불가'}
    r5, r21 = ret_n(close, 5), ret_n(close, 21)
    e10, e21 = ema(close, 10), ema(close, 21)
    sma50 = close.rolling(50).mean()
    above10_series = close > e10          # 각 거래일 종가 vs 그날의 EMA10
    rvol = rvol20_series(vol)
    bullish = df['Close'] > df['Open']    # 양봉 = 종가 > 시가 (전일 대비 상승과 다름)
    bull_rvol = (bullish & (rvol >= 1.5)).iloc[-5:]

    tr = pd.concat([df['High'] - df['Low'],
                    (df['High'] - close.shift()).abs(),
                    (df['Low'] - close.shift()).abs()], axis=1).max(axis=1)
    gaps_pct = (df['Open'] / close.shift() - 1).abs().iloc[-20:] * 100
    avg_sh = float(vol.iloc[-20:].mean())
    c = float(close.iloc[-1])

    b1, b2 = bench.names[0], bench.names[-1]     # 벤치마크 1개면 B1 = B2 = 평균
    rs5b, rs21b = r5 - bench.blend5, r21 - bench.blend21
    sma50_v = None if pd.isna(sma50.iloc[-1]) else float(sma50.iloc[-1])
    avg_val = float((vol * close).iloc[-20:].mean())
    liq_ok = ((liquidity_shares is None or avg_sh >= liquidity_shares)
              and (liquidity_value is None or avg_val >= liquidity_value))

    return {
        **out, 'status': 'FULL',
        'close': c, 'ret5': r5, 'ret21': r21, 'adr20Pct': adr,
        'atr20Pct': float(tr.iloc[-20:].mean()) / c * 100,
        'gapDays20': int((gaps_pct >= max(adr, 3.0)).sum()),
        'rs5B1': r5 - bench.ret5[b1], 'rs5B2': r5 - bench.ret5[b2], 'rs5Blend': rs5b,
        'rs21B1': r21 - bench.ret21[b1], 'rs21B2': r21 - bench.ret21[b2], 'rs21Blend': rs21b,
        'nrs5': rs5b / adr, 'nrs21': rs21b / adr,
        'ema10': float(e10.iloc[-1]), 'ema21': float(e21.iloc[-1]), 'sma50': sma50_v,
        'aboveEma10': bool(above10_series.iloc[-1]),
        'aboveEma21': bool(close.iloc[-1] > e21.iloc[-1]),
        'aboveSma50': None if sma50_v is None else c > sma50_v,
        'aboveEma10Prev5': bool(above10_series.iloc[-6]),
        'ema10DaysOfLast5': int(above10_series.iloc[-5:].sum()),
        'rvol20': None if pd.isna(rvol.iloc[-1]) else float(rvol.iloc[-1]),
        'hasBullishRvol15InLast5': bool(bull_rvol.any()),
        'avgShares20': avg_sh, 'avgValue20': avg_val,
        'liquidityOk': liq_ok,
    }


# ── 섹터 ─────────────────────────────────────────────────

def sector_metrics(rows: list, theme_of: dict, main_codes: set, bench: Bench,
                   taxonomy_version: str = '') -> list:
    """rows: stock_metrics 결과. theme_of: {ticker: primaryTheme}. 메인 테마만 점수·등급 대상."""
    by_theme: dict = {}
    for r in rows:
        code = theme_of.get(r['ticker'], 'UNCLASSIFIED')
        if code == 'UNCLASSIFIED':
            continue
        by_theme.setdefault(code, []).append(r)

    sectors = []
    for code, members in by_theme.items():
        good = [m for m in members if m['status'] == 'FULL']
        n, nv = len(members), len(good)
        s = {
            'asOf': bench.asof.strftime('%Y-%m-%d'), 'theme': code, 'inMainRanking': code in main_codes,
            'memberCount': n, 'validCount': nv, 'coverage': nv / n if n else 0.0,
            'members': sorted(m['ticker'] for m in members),
            'invalidMembers': {m['ticker']: m['status'] for m in members if m['status'] != 'FULL'},
            'taxonomyVersion': taxonomy_version,
        }
        g = lambda k: [m.get(k) for m in good]
        s.update({
            'medianRet5': _median(g('ret5')), 'medianRet21': _median(g('ret21')),
            'medianRs5': _median(g('rs5Blend')), 'medianRs21': _median(g('rs21Blend')),
            'medianRs5B1': _median(g('rs5B1')), 'medianRs5B2': _median(g('rs5B2')),
            'medianRs21B1': _median(g('rs21B1')), 'medianRs21B2': _median(g('rs21B2')),
            'medianNrs5': _median(g('nrs5')), 'medianNrs21': _median(g('nrs21')),
            'medianAdr20': _median(g('adr20Pct')),
            'pctAbove10': _ratio(g('aboveEma10')),
            'pctAbove10Prev5': _ratio(g('aboveEma10Prev5')),
            'pctEma10Persist3Of5': _ratio([d >= 3 for d in g('ema10DaysOfLast5')]),
            'pctAbove21': _ratio(g('aboveEma21')),
            'pctBullishRvol15_5D': _ratio(g('hasBullishRvol15InLast5')),
            'pctPositive5D': _ratio([r > 0 for r in g('ret5')]),
            'pctBeatB1_5D': _ratio([r > bench.ret5[bench.names[0]] for r in g('ret5')]),
            'pctBeatB2_5D': _ratio([r > bench.ret5[bench.names[-1]] for r in g('ret5')]),
        })
        s['ema10ChangePp'] = (None if s['pctAbove10'] is None or s['pctAbove10Prev5'] is None
                              else 100 * (s['pctAbove10'] - s['pctAbove10Prev5']))
        gate = []
        if not s['inMainRanking']:
            gate.append('메인 랭킹 밖 테마 (별도 표)')
        if n < MIN_SECTOR_N:
            gate.append(f'구성 종목 {n}개 < {MIN_SECTOR_N}')
        if s['coverage'] < MIN_COVERAGE:
            gate.append(f'데이터 확보 {nv}/{n} ({s["coverage"]:.0%}) < {MIN_COVERAGE:.0%}')
        s['eligible'] = not gate
        s['gatingReasons'] = gate
        s['score'], s['scoreParts'], s['grade'] = None, {}, 'UNVERIFIED'
        sectors.append(s)

    _score_and_grade([s for s in sectors if s['eligible']], bench)
    order = {'A_CONFIRMED': 0, 'B_EMERGING': 1, 'C_WATCH': 2, 'UNVERIFIED': 3}
    return sorted(sectors, key=lambda s: (order[s['grade']], -(s['score'] or -1), -s['coverage']))


def _score_and_grade(eligible: list, bench: Bench) -> None:
    if not eligible:
        return
    pct = {f: percentile_rank({s['theme']: s[src] for s in eligible})
           for f, src in [('rs5', 'medianRs5'), ('nrs5', 'medianNrs5'),
                          ('rs21', 'medianRs21'), ('nrs21', 'medianNrs21')]}
    preliminary = len(eligible) < 4
    for s in eligible:
        parts = {f: (None if pct[f][s['theme']] is None else SCORE_WEIGHTS[f] * pct[f][s['theme']])
                 for f in ('rs5', 'nrs5', 'rs21', 'nrs21')}
        for f, src in [('above10', 'pctAbove10'), ('persist', 'pctEma10Persist3Of5'),
                       ('above21', 'pctAbove21'), ('rvolBreadth', 'pctBullishRvol15_5D'),
                       ('participation', 'pctPositive5D')]:
            parts[f] = None if s[src] is None else SCORE_WEIGHTS[f] * s[src]
        s['scoreParts'] = parts
        if any(v is None for v in parts.values()):
            s['gatingReasons'].append('평가 가능 섹터 2개 미만 — 백분위 산출 불가'
                                      if pct['rs5'][s['theme']] is None else '일부 지표 계산 불가')
            continue
        s['score'] = sum(parts.values())
        if preliminary:
            s['flags'] = ['SCORE_PRELIMINARY']
        pair = '·'.join(bench.names)
        a_checks = {
            '점수 ≥75': s['score'] >= 75,
            f'5일 수익률 > {pair}': s['medianRet5'] > max(bench.ret5.values()),
            f'21일 수익률 > {pair}': s['medianRet21'] > max(bench.ret21.values()),
            '10EMA 위 ≥60%': s['pctAbove10'] >= 0.6,
            '10EMA 3/5 유지 ≥50%': s['pctEma10Persist3Of5'] >= 0.5,
            '21EMA 위 ≥40%': s['pctAbove21'] >= 0.4,
        }
        b_checks = {
            '점수 ≥60': s['score'] >= 60,
            '5일 RS(평균) > 0': s['medianRs5'] > 0,
            '10EMA 위 ≥40% 또는 5일 전 대비 +20%p': (s['pctAbove10'] >= 0.4 or
                                                (s['ema10ChangePp'] or 0) >= 20),
        }
        s['gateChecks'] = {'A': a_checks, 'B': b_checks}
        if all(a_checks.values()):
            s['grade'] = 'A_CONFIRMED'
        elif all(b_checks.values()):
            s['grade'] = 'B_EMERGING'
        else:
            s['grade'] = 'C_WATCH'


# ── 개별주 주도주 점수 (명세 8장) ─────────────────────────

def leader_scores(rows: list, theme_of: dict, main_codes: set, sectors: list) -> list:
    """메인 테마 FULL 종목에 섹터 내 RS와 주도주 점수(촉매 15점 제외, 85점 만점)를 붙인다."""
    med = {s['theme']: (s['medianRet5'], s['medianRet21']) for s in sectors}
    pool = [r for r in rows if r['status'] == 'FULL' and theme_of.get(r['ticker']) in main_codes]
    for r in pool:
        m5, m21 = med.get(theme_of[r['ticker']], (None, None))
        r['rsSector5'] = None if m5 is None else r['ret5'] - m5
        r['rsSector21'] = None if m21 is None else r['ret21'] - m21
        r['nrsSector5'] = None if r['rsSector5'] is None else r['rsSector5'] / r['adr20Pct']
        r['nrsSector21'] = None if r['rsSector21'] is None else r['rsSector21'] / r['adr20Pct']
    keys = ['rs5Blend', 'nrs5', 'rsSector5', 'nrsSector5', 'rs21Blend', 'nrs21', 'rsSector21', 'nrsSector21']
    pct = {k: percentile_rank({r['ticker']: r.get(k) for r in pool}) for k in keys}
    for r in pool:
        t = r['ticker']
        p = lambda k: 0.0 if pct[k][t] is None else pct[k][t]
        aligned = (r['sma50'] is not None and r['ema10'] > r['ema21'] > r['sma50'])
        parts = {
            'rs5': 5 * (p('rs5Blend') + p('nrs5') + p('rsSector5') + p('nrsSector5')),
            'rs21': 5 * (p('rs21Blend') + p('nrs21') + p('rsSector21') + p('nrsSector21')),
            'structure': 5 * (r['aboveEma10'] + r['aboveEma21'] + bool(r['aboveSma50']) + aligned),
            'persist': 15 * r['ema10DaysOfLast5'] / 5,
            'volume': 5 * r['hasBullishRvol15InLast5'] + 5 * r['liquidityOk'],
        }
        r['leaderParts'] = parts
        r['leaderScore'] = sum(parts.values())
        r['leaderType'] = _leader_type(r)
        r['entrySignal'] = 'WATCH' if r['leaderType'] in ('LEADER_TREND', 'EMERGING', 'REBOUND') else None
    return pool


def _leader_type(r: dict) -> str:
    if r['rs5Blend'] > 0 and r['rs21Blend'] > 0 and r['aboveEma10'] and r['aboveEma21'] and r['aboveSma50']:
        return 'LEADER_TREND'      # 기존 강세 유지형
    if r['ret21'] <= -15 and r['rs5Blend'] > 0:
        return 'REBOUND'           # 낙폭 반등형
    if r['rs5Blend'] > 0 and r['aboveEma10']:
        return 'EMERGING'          # 초기 전환형 (21일·50SMA 미회복 가능)
    return 'OTHER'


def attach_trend(latest: list, earlier: list) -> None:
    """latest: 기준일 섹터 결과. earlier: [1주 전 섹터 결과, 2주 전 섹터 결과] (없으면 빈 리스트).

    각 섹터에 붙이는 값:
      history       — [{asof, pctAbove10, pctAbove21, score, medianNrs5, pctBullishRvol15_5D}] 오래된 순 (3주 추이)
      scoreChange   — 1주 전 대비 점수 변화 (둘 다 점수가 있을 때만)
      improving     — 1주 전 대비 10EMA 위 비율 +10%p 이상 · 5일 RS/ADR 상승 · RVOL 확산 비감소가
                      동시에 성립 (명세 '변화 감지': 세 가지가 함께 좋아지는 섹터를 따로 강조)
    """
    keys = ('pctAbove10', 'pctAbove21', 'score', 'medianNrs5', 'pctBullishRvol15_5D')
    past = [{s['theme']: s for s in rep} for rep in earlier]
    for s in latest:
        hist = []
        for m in reversed(past):
            p = m.get(s['theme'])
            if p:
                hist.append({'asof': p['asOf'], **{k: p.get(k) for k in keys}})
        hist.append({'asof': s['asOf'], **{k: s.get(k) for k in keys}})
        s['history'] = hist
        prev = past[0].get(s['theme']) if past else None
        s['scoreChange'] = (s['score'] - prev['score']
                            if prev and s['score'] is not None and prev['score'] is not None else None)
        s['improving'] = bool(
            prev and None not in (s['pctAbove10'], prev['pctAbove10'], s['medianNrs5'], prev['medianNrs5'],
                                  s['pctBullishRvol15_5D'], prev['pctBullishRvol15_5D'])
            and s['pctAbove10'] - prev['pctAbove10'] >= 0.10
            and s['medianNrs5'] > prev['medianNrs5']
            and s['pctBullishRvol15_5D'] >= prev['pctBullishRvol15_5D'])


def grade_bucket(s: dict) -> str:
    """A/B 외 섹터를 명세대로 관찰(45~59점)·후순위(45점 미만)로 나눈다."""
    if s['grade'] in ('A_CONFIRMED', 'B_EMERGING', 'UNVERIFIED'):
        return s['grade']
    return 'C_WATCH' if (s['score'] or 0) >= 45 else 'D_LOW'


@dataclass
class Report:
    asof: str
    bench: Bench
    stocks: list
    sectors: list
    leaders: list
    coverage: dict = field(default_factory=dict)


def run(bars: dict, universe: list, theme_of: dict, main_codes: set, asof,
        taxonomy_version: str = '', bench_names: tuple = BENCHMARKS,
        liquidity_shares: int | None = LIQUIDITY_SHARES,
        liquidity_value: float | None = None) -> Report:
    """universe: 원본 유니버스 티커(벤치마크 제외). 외부 종목은 넣지 않는다."""
    bench = build_bench(bars, asof, bench_names)
    universe = [t for t in dict.fromkeys(universe) if t not in bench_names]
    rows = [stock_metrics(t, bars.get(t), bench, liquidity_shares, liquidity_value) for t in universe]
    for r in rows:
        r['primaryTheme'] = theme_of.get(r['ticker'], 'UNCLASSIFIED')
    sectors = sector_metrics(rows, theme_of, main_codes, bench, taxonomy_version)
    leaders = leader_scores(rows, theme_of, main_codes, sectors)
    status_count: dict = {}
    for r in rows:
        status_count[r['status']] = status_count.get(r['status'], 0) + 1
    coverage = {'total': len(rows), 'full': status_count.get('FULL', 0), 'byStatus': status_count,
                'classified': sum(theme_of.get(t, 'UNCLASSIFIED') != 'UNCLASSIFIED' for t in universe)}
    return Report(asof=bench.asof.strftime('%Y-%m-%d'), bench=bench, stocks=rows,
                  sectors=sectors, leaders=leaders, coverage=coverage)
