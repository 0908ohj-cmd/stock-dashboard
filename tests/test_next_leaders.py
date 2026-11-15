import numpy as np
import pandas as pd
import pytest

from strategy import next_leaders as nl
from strategy import sector_strength as ss


def _row(t, theme, score, **kw):
    base = {'ticker': t, 'primaryTheme': theme, 'nextScore': score, 'aboveEma21': True, 'aboveEma10': True,
            'sincePeakExcess': 1.0, 'offHigh': -5.0, 'sinceJjinExcess': None, 'rs5Blend': 1.0}
    base.update(kw)
    return base


def test_섹터는_3종목_이상만_순위를_매긴다():
    pool = [_row('A', 'BIO', 90), _row('B', 'BIO', 80), _row('C', 'BIO', 75),
            _row('X', 'ENT', 99), _row('Y', 'ENT', 98)]
    ranked, thin = nl.rank_sectors(pool)
    assert [s['theme'] for s in ranked] == ['BIO']
    assert [s['theme'] for s in thin] == ['ENT']


def test_섹터점수는_중앙값60_강한종목비율40():
    pool = [_row('A', 'S', 90), _row('B', 'S', 60), _row('C', 'S', 50)]
    s = nl.rank_sectors(pool)[0][0]
    assert s['medianScore'] == 60
    assert s['strongShare'] == pytest.approx(1 / 3)
    assert s['score'] == pytest.approx(0.6 * 60 + 0.4 * 100 / 3)


def test_한종목만_강한_섹터는_여러종목이_강한_섹터보다_뒤():
    pool = [_row('A', 'ONE', 100), _row('B', 'ONE', 40), _row('C', 'ONE', 40),
            _row('D', 'MANY', 72), _row('E', 'MANY', 71), _row('F', 'MANY', 70)]
    ranked, _ = nl.rank_sectors(pool)
    assert [s['theme'] for s in ranked] == ['MANY', 'ONE']


def _index(closes, end='2026-11-06'):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({'Open': closes, 'High': closes * 1.01, 'Low': closes * 0.99,
                         'Close': closes, 'Volume': np.full(len(closes), 1e6)}, index=idx)


def test_지수가_21EMA_아래로_빠지면_조정_국면():
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 20))
    ph = nl.detect_phase(_index(closes), '2026-11-06')
    assert ph['mode'] == 'correction'
    assert ph['peakDate'] is not None and ph['indexSincePeak'] < 0


def test_꾸준한_상승은_상승추세_국면():
    ph = nl.detect_phase(_index(np.linspace(100, 160, 120)), '2026-11-06')
    assert ph['mode'] == 'uptrend'


def test_수동_기산점과_찐반등일이_자동값을_대체한다():
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 20))
    idf = _index(closes)
    auto = nl.detect_phase(idf, '2026-11-06')
    ph = nl.detect_phase(idf, '2026-11-06', peak='2026-10-01', jjin='2026-11-01')   # 11/01 일요일
    assert ph['manual'] and not auto['manual']
    assert ph['peakDate'] == pd.Timestamp('2026-10-01') != auto['peakDate']
    assert ph['jjinDate'] == pd.Timestamp('2026-10-30')     # 휴장일 → 직전 거래일
    assert ph['mode'] == 'rebound' and ph['label'] == 'DAY7'  # 10/30 이후 5거래일 (11/2~11/6)
    assert ph['indexSincePeak'] == pytest.approx(100 * (100 / idf.loc['2026-10-01', 'Close'] - 1))
    assert ph['autoPeakDate'] == auto['peakDate']


def test_상승추세가_돼도_마지막_기산점과_찐반등을_유지한다():
    # 조정 → 찐반등 → 21EMA 회복 후 오래 상승 (DAY7 지나 Normal)
    closes = (list(np.linspace(100, 130, 100)) + list(np.linspace(129, 105, 12))
              + [112] + list(np.linspace(116, 150, 30)))
    idf = _index(closes)
    j = idf.index[112]                      # 찐반등일: 장중 저가가 21EMA 아래 + 양봉 + ADR 이상 상승
    idf.loc[j, ['Open', 'Low']] = [104.0, 103.0]
    ph = nl.detect_phase(idf, '2026-11-06')
    assert ph['mode'] == 'uptrend'
    assert ph['jjinDate'] == j
    assert ph['jjinDate'] is not None and ph['peakDate'] is not None
    assert ph['scoring'] == 'rebound'


def test_상승추세에서_기산점만_지정하면_버틴힘_화면():
    idf = _index(np.linspace(100, 160, 120))
    ph = nl.detect_phase(idf, '2026-11-06', peak='2026-10-15')
    assert ph['mode'] == 'correction' and ph['jjinDate'] is None
    ph2 = nl.detect_phase(idf, '2026-11-06', jjin='2026-11-02')
    assert ph2['mode'] == 'rebound' and ph2['peakDate'] is not None   # 자동 조정이 없어도 기산점을 잡는다
    # 기산점은 찐반등일 '이전' 최고 종가 — 상승 추세에선 찐반등일 당일이 최고라 같은 날이 되면 빈 구간(#29)
    assert ph2['peakDate'] < ph2['jjinDate'] and ph2['warning'] is None
    # 수동 구간으로도 종목 점수가 계산돼야 한다 (자동 조정 시작일이 없을 때 ADR 기준 시점 보완)
    stock = _index(np.linspace(100, 170, 120))
    rows = [{'ticker': 'S', 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': True,
             'hasBullishRvol15InLast5': False}]
    for p in (ph, ph2):
        out = nl.score_stocks(rows, {'S': stock}, idf, {'S': 'X'}, {'X'}, p, '2026-11-06')
        assert out and out[0]['sincePeakExcessAdr'] is not None


def _day3_index():
    """자동 DAY3 — 조정 → 찐반등(뒤에서 2번째 날) → 다음 날이 기준일."""
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 105, 12)) + [112, 113]
    idf = _index(closes)
    j = idf.index[112]
    idf.loc[j, ['Open', 'Low']] = [104.0, 103.0]
    return idf, j


def _assert_formattable(ph):
    """배너가 포맷하는 날짜·수치가 국면에 맞게 모두 있어야 한다 (None 포맷 → TypeError, #29)."""
    if ph['mode'] == 'rebound':
        assert ph['jjinDate'] is not None and ph['peakDate'] is not None and ph['indexSinceJjin'] is not None
    if ph['mode'] == 'correction':
        assert ph['peakDate'] is not None and ph['indexSincePeak'] is not None


def test_자동_DAY3에서_기산점을_찐반등일_이후로_지정하면_조정_국면과_경고():
    idf, j = _day3_index()
    asof = idf.index[-1]
    auto = nl.detect_phase(idf, asof)
    assert auto['label'] == 'DAY3' and auto['jjinDate'] == j and auto.get('warning') is None
    for peak in (j, idf.index[-1]):         # 찐반등일 당일 · 그 다음날
        ph = nl.detect_phase(idf, asof, peak=peak)
        _assert_formattable(ph)
        assert ph['mode'] == 'correction' and ph['label'] == '수동 기산점'
        assert ph['peakDate'] == peak and ph['jjinDate'] is None and ph['scoring'] == 'correction'
        assert ph['warning'] and f'{j:%m/%d}' in ph['warning']     # 자동 찐반등을 무시했다고 알린다


def test_조정중_찐반등일만_기산점보다_앞으로_지정하면_수동값을_무시하고_경고():
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 20))
    idf = _index(closes)
    asof = idf.index[-1]
    auto = nl.detect_phase(idf, asof)
    early = auto['peakDate'] - pd.Timedelta(days=7)
    for jjin in (early, auto['peakDate']):      # 기산점보다 앞 · 같은 날
        ph = nl.detect_phase(idf, asof, jjin=jjin)
        _assert_formattable(ph)
        assert ph['warning']
        assert not ph['manual']
        assert {k: ph[k] for k in ('mode', 'label', 'scoring', 'peakDate', 'jjinDate')} == \
               {k: auto[k] for k in ('mode', 'label', 'scoring', 'peakDate', 'jjinDate')}
    # 무시된 결과로 점수를 내면 '덜 빠짐'이 종목마다 달라야 한다 (빈 구간이면 전원 0 → 동점)
    bars, rows = {}, []
    for k, end in enumerate((120, 110, 90)):
        s = _index(list(np.linspace(100, 130, 100)) + list(np.linspace(129, end, 20)))
        bars[f'S{k}'] = s
        rows.append({'ticker': f'S{k}', 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': True,
                     'hasBullishRvol15InLast5': False})
    out = nl.score_stocks(rows, bars, idf, {t: 'X' for t in bars}, {'X'}, ph, asof)
    assert len({r['sincePeakExcessAdr'] for r in out}) == 3


def test_기산점과_찐반등일을_둘다_지정했는데_순서가_뒤집히면_기산점을_무시한다():
    idf, j = _day3_index()
    asof = idf.index[-1]
    auto = nl.detect_phase(idf, asof)
    # 같은 주말(토·일)을 고르면 둘 다 직전 금요일로 맞춰져 같은 날이 된다 — 화면 검사(peak >= jjin)를 빠져나가는 경우
    sat = pd.Timestamp('2026-10-31')
    fri = pd.Timestamp('2026-10-30')
    assert auto['peakDate'] < fri < asof
    ph = nl.detect_phase(idf, asof, peak=sat + pd.Timedelta(days=1), jjin=sat)
    _assert_formattable(ph)
    assert ph['warning']
    # 화면 규칙과 같게 기산점만 버리고 찐반등일은 살린다 → 기산점은 자동값
    assert ph['jjinDate'] == fri and ph['peakDate'] == auto['peakDate'] and ph['mode'] == 'rebound'


def test_RS_구간은_기산점이_찐반등일보다_앞일_때만_성립():
    from strategy.rs_correction import is_valid_rs_range
    a, b = pd.Timestamp('2026-10-01'), pd.Timestamp('2026-10-10')
    assert is_valid_rs_range(a, b)
    assert not is_valid_rs_range(b, a)
    assert not is_valid_rs_range(a, a)            # 같은 날 = 빈 구간
    assert is_valid_rs_range(None, a) and is_valid_rs_range(a, None) and is_valid_rs_range(None, None)
    assert is_valid_rs_range('2026-10-01', '2026-10-10')


def test_덜빠짐은_ADR_보정값으로_순위를_매긴다():
    # 두 종목 모두 지수 대비 +10%p지만, 변동성(ADR)이 작은 쪽이 더 '버틴' 것
    closes = np.array(list(np.linspace(100, 130, 100)) + list(np.linspace(129, 110, 20)))
    idf = _index(list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 20)))
    calm, wild = _index(closes), _index(closes)
    wild['High'], wild['Low'] = closes * 1.05, closes * 0.95
    peak = idf['Close'].idxmax()
    phase = {'mode': 'correction', 'correctionStart': peak, 'peakDate': peak,
             'jjinDate': None, 'indexSinceJjin': None}
    rows = [{'ticker': t, 'status': 'FULL', 'adr20Pct': adr, 'aboveEma21': False,
             'hasBullishRvol15InLast5': False} for t, adr in (('CALM', 2.0), ('WILD', 10.5))]
    out = {r['ticker']: r for r in nl.score_stocks(rows, {'CALM': calm, 'WILD': wild}, idf,
                                                   {'CALM': 'X', 'WILD': 'X'}, {'X'}, phase, '2026-11-06')}
    assert out['CALM']['sincePeakExcess'] == pytest.approx(out['WILD']['sincePeakExcess'])
    assert out['CALM']['nextParts']['덜 빠짐'] > out['WILD']['nextParts']['덜 빠짐']


def test_찐반등_이후에는_조정구간과_반등구간이_겹치지_않는다():
    idf = _index(list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 15)) + list(np.linspace(102, 110, 5)))
    stock = _index(list(np.linspace(100, 130, 100)) + list(np.linspace(129, 115, 15)) + list(np.linspace(118, 140, 5)))
    peak, jjin, base = idf['Close'].idxmax(), idf.index[115], idf.index[114]
    phase = {'mode': 'rebound', 'correctionStart': peak, 'peakDate': peak, 'jjinDate': jjin, 'jjinBase': base,
             'indexSinceJjin': 100 * (idf['Close'].iloc[-1] / idf.loc[base, 'Close'] - 1)}
    rows = [{'ticker': 'S', 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': True, 'hasBullishRvol15InLast5': False}]
    r = nl.score_stocks(rows, {'S': stock}, idf, {'S': 'X'}, {'X'}, phase, '2026-11-06')[0]
    ret = lambda df, a, b: 100 * (df.loc[b, 'Close'] / df.loc[a, 'Close'] - 1)
    assert r['sincePeakExcess'] == pytest.approx(ret(stock, peak, jjin) - ret(idf, peak, jjin), abs=0.01)
    # 반등 구간은 찐반등 전날 종가부터 — 찐반등 당일 상승 포함
    assert r['sinceJjinExcess'] == pytest.approx(ret(stock, base, idf.index[-1]) - ret(idf, base, idf.index[-1]))


def test_찐반등_당일_저녁에도_반등_세기가_보인다():
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 15)) + [106]
    idf = _index(closes)
    ph = nl.detect_phase(idf, idf.index[-1], jjin=idf.index[-1])      # 기준일 = 찐반등 당일
    assert ph['jjinBase'] == idf.index[-2]
    assert ph['indexSinceJjin'] == pytest.approx(6.0)                  # 0이 아니라 당일 상승 +6%


def _sec(theme, above10, nrs5, rvol, score, asof='2026-11-06'):
    return {'theme': theme, 'asOf': asof, 'pctAbove10': above10, 'medianNrs5': nrs5,
            'pctBullishRvol15_5D': rvol, 'score': score}


def test_변화감지는_세가지가_동시에_좋아질때만():
    latest = [_sec('UP', 0.5, 1.0, 0.4, 70), _sec('HALF', 0.5, 0.1, 0.4, 70)]
    w1 = [_sec('UP', 0.3, 0.5, 0.3, 55, '2026-10-30'), _sec('HALF', 0.3, 0.5, 0.3, 60, '2026-10-30')]
    w2 = [_sec('UP', 0.2, 0.2, 0.1, 40, '2026-10-23')]
    ss.attach_trend(latest, [w1, w2])
    up, half = latest
    assert up['improving'] is True
    assert half['improving'] is False             # RS/ADR이 오히려 떨어짐
    assert [h['pctAbove10'] for h in up['history']] == [0.2, 0.3, 0.5]
    assert up['scoreChange'] == 15


def _rot(t, theme, score):
    return {'ticker': t, 'primaryTheme': theme, 'rotScore': score, 'aboveEma21': True, 'aboveEma10': True}


def test_새_순환매는_2주전_약했다가_지금_강해진_섹터만():
    now = ([_rot(f'S{i}', 'SPACE', 80) for i in range(3)]          # 30 → 80: 🆕
           + [_rot(f'O{i}', 'OLD', 85) for i in range(3)]          # 80 → 85: 기존 강세
           + [_rot(f'W{i}', 'WEAK', 55) for i in range(3)])        # 30 → 55: 아직 60 미만
    prev = ([_rot(f'S{i}', 'SPACE', 30) for i in range(3)] + [_rot(f'O{i}', 'OLD', 80) for i in range(3)]
            + [_rot(f'W{i}', 'WEAK', 30) for i in range(3)])
    ranked, _ = nl.rotation_sectors(now, prev)
    by = {s['theme']: s for s in ranked}
    assert by['SPACE']['isNew'] and not by['OLD']['isNew'] and not by['WEAK']['isNew']
    assert ranked[0]['theme'] == 'SPACE'                  # 🆕이 점수 높은 기존 강세보다 앞
    assert by['SPACE']['scoreRise'] == pytest.approx(by['SPACE']['score'] - by['SPACE']['prevScore'])


def test_순환매_점수는_이번주_강한_종목이_높다():
    n = 140
    idf = _index(np.full(n, 100.0))
    base = np.full(n, 100.0)
    lag = base.copy(); lag[-5:] = np.linspace(102, 110, 5)       # 지난주 평평 → 이번 주 급가속
    early = base.copy(); early[-10:-5] = np.linspace(102, 110, 5); early[-5:] = 110   # 지난주에 이미 오르고 멈춤
    bars = {'LAG': _index(lag), 'EARLY': _index(early)}
    for df in bars.values():
        df['High'], df['Low'] = df['Close'] * 1.01, df['Close'] * 0.99
    rows = [{'ticker': t, 'status': 'FULL', 'aboveEma10': True, 'aboveEma21': True, 'ema10DaysOfLast5': 5,
             'hasBullishRvol15InLast5': False} for t in bars]
    out = {x['ticker']: x for x in nl.rotation_scores(rows, bars, idf, {'LAG': 'X', 'EARLY': 'X'}, {'X'},
                                                       '2026-11-06')}
    assert out['LAG']['rotRs5'] > out['EARLY']['rotRs5']
    assert out['LAG']['rotScore'] > out['EARLY']['rotScore']
    assert set(out['LAG']['rotParts']) == set(nl.ROT_WEIGHTS)


def test_장기_추세는_200일선_방향으로_점수():
    assert nl.trend_points(True) == 1.0          # 200일선 상승 중
    assert nl.trend_points(False) == 0.0
    assert nl.trend_points(None) == 0.0          # 이력 부족
    up = _index(np.linspace(50, 150, 260))
    assert nl.sma200_rising(up) is True
    # 주가가 200일선 아래로 눌려도 200일선이 오르는 중이면 상승
    dip = _index(list(np.linspace(50, 150, 250)) + [80.0] * 10)
    assert nl.sma200_gap(dip) < 0 and nl.sma200_rising(dip) is True
    # 주가가 200일선 위여도 200일선이 내려가는 중이면 하락
    bounce = _index(list(np.linspace(150, 50, 250)) + [140.0] * 10)
    assert nl.sma200_gap(bounce) > 0 and nl.sma200_rising(bounce) is False
    assert nl.sma200_rising(_index(np.full(210, 100.0))) is None
    df = _index(list(np.full(199, 100.0)) + [110.0])
    assert nl.sma200_gap(df) == pytest.approx(100 * (110 / (199 * 100 + 110) * 200 - 1))
    assert nl.sma200_gap(_index(np.full(150, 100.0))) is None


def test_세_화면_공통_재료_일관성():
    # 거래량 우위·21EMA·RS 가속은 검증에서 효과가 없거나 역효과 → 어느 점수에도 없다
    for w in list(nl.WEIGHTS.values()) + [nl.ROT_WEIGHTS]:
        assert sum(w.values()) == 100
        assert not {'거래량 우위', '21EMA 위', 'RS 가속'} & set(w)
        assert '고점 근접' in w and '장기 추세' in w


def test_메인_밖_테마_종목도_종목_순위에는_들어간다():
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 20))
    idf = _index(closes)
    peak = idf['Close'].idxmax()
    phase = {'mode': 'correction', 'correctionStart': peak, 'peakDate': peak, 'jjinDate': None, 'indexSinceJjin': None}
    rows = [{'ticker': t, 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': False, 'hasBullishRvol15InLast5': False}
            for t in ('MAIN', 'OTHER', 'NONE')]
    bars = {t: _index(closes) for t in ('MAIN', 'OTHER', 'NONE')}
    out = nl.score_stocks(rows, bars, idf, {'MAIN': 'TECH', 'OTHER': 'CONSUMER_OTHER', 'NONE': 'UNCLASSIFIED'},
                          {'TECH'}, phase, '2026-11-06')
    assert sorted(r['ticker'] for r in out) == ['MAIN', 'OTHER']     # 미분류만 빠진다


def test_차기_주도주_배점은_100점이고_21EMA는_없다():
    for w in nl.WEIGHTS.values():
        assert sum(w.values()) == 100
        assert '21EMA 위' not in w
    assert nl.WEIGHTS['rebound']['찐반등 후 회복'] > nl.WEIGHTS['rebound']['덜 빠짐']


def test_관찰_후순위_구분():
    assert ss.grade_bucket({'grade': 'C_WATCH', 'score': 50}) == 'C_WATCH'
    assert ss.grade_bucket({'grade': 'C_WATCH', 'score': 44}) == 'D_LOW'
    assert ss.grade_bucket({'grade': 'B_EMERGING', 'score': 61}) == 'B_EMERGING'


def _bars(n, bull_days=()):
    """n거래일 평범한 봉(음봉·거래량 100), bull_days(뒤에서 몇 번째) 날만 거래량 300 양봉."""
    idx = pd.bdate_range('2026-01-01', periods=n)
    o = np.full(n, 10.0); c = np.full(n, 9.9); v = np.full(n, 100.0)
    for k in bull_days:
        c[n - k] = 10.5; v[n - k] = 300.0
    return pd.DataFrame({'Open': o, 'High': np.maximum(o, c) + .1, 'Low': np.minimum(o, c) - .1,
                         'Close': c, 'Volume': v}, index=idx)


def test_hot_sectors_절반이상_렐볼양봉이면_지금_불():
    bars = {'A': _bars(80, (1,)), 'B': _bars(80, (2,)), 'C': _bars(80), 'D': _bars(80, (3,))}
    theme = {t: 'OPT' for t in bars}
    h = nl.hot_sectors(bars, theme, bars['A'].index[-1], min_aligned=0)
    assert h['OPT']['now'] and h['OPT']['share'] == 0.75
    assert sorted(h['OPT']['tickers']) == ['A', 'B', 'D']


def test_hot_sectors_지나간_불도_이력에_남는다():
    # 15거래일 전에 3종목이 렐볼 양봉 → 지금은 아니지만 이력에 남음
    bars = {t: _bars(80, (15,)) for t in 'ABC'}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1], min_aligned=0)
    assert not h['OPT']['now']
    assert h['OPT']['last'] == bars['A'].index[-15] and h['OPT']['daysAgo'] == 14


def test_hot_sectors_3종목_미만이나_미분류는_빠진다():
    bars = {t: _bars(80, (1,)) for t in 'ABC'}
    assert nl.hot_sectors(bars, {'A': 'X', 'B': 'X', 'C': 'UNCLASSIFIED'}, bars['A'].index[-1], min_aligned=0) == {}

def test_pp_준비도_배점_합100_200일선_단계():
    assert sum(nl.PP_WEIGHTS.values()) == 100
    assert '장기 추세' not in nl.PP_WEIGHTS


def test_hot_sectors_HOT_GAP_안_재점화는_같은_불이라_편입일은_그_구간의_첫_켜짐():
    bars = {t: _bars(80, (15, 3)) for t in 'ABC'}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1], min_aligned=0)['OPT']
    assert h['since'] == bars['A'].index[-15] and not h['startedBefore']


def _hot_since_by_day(bars, days):
    """기준일을 하루씩 옮기며 (편입일, startedBefore, last) — 마지막 days+1거래일."""
    idx = bars['A'].index
    out = []
    for asof in idx[-(days + 1):]:
        h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, asof, min_aligned=0)['OPT']
        out.append((h['since'], h['startedBefore'], h['last']))
    return out


def test_hot_sectors_편입일은_창이_밀려도_안_바뀐다():
    # 3종목 섹터가 기준일(day0) 기준 T−29·T−8에 🔥 — 5거래일 뒤까지 매일 편입일이 같아야 한다 (#30).
    # 창(30거래일) 안 첫 켜짐을 편입일로 쓰면 T−29가 창 밖으로 밀리며 하루씩 늦어지다 T−8로 건너뛴다.
    n = 85                                                  # day0 = 뒤에서 6번째 날
    bars = {t: _bars(n, (35, 14)) for t in 'ABC'}
    t8 = bars['A'].index[n - 14]
    seq = _hot_since_by_day(bars, 5)
    assert {s for s, _, _ in seq} == {t8}                   # 현재 🔥 구간(T−8 재점화)의 시작
    assert not any(b for _, b, _ in seq)
    assert all(last == s for s, _, last in seq)             # 10EMA 배지·🔥 시작일도 같은 날


def test_hot_sectors_창보다_전에_시작한_불은_더_긴_이력으로_시작일을_찾는다():
    # day0 기준 T−35부터 8거래일마다 재점화(HOT_GAP 안이라 같은 🔥) — 시작이 30거래일 창보다 앞
    n = 100
    bars = {t: _bars(n, (41, 33, 25, 17, 9)) for t in 'ABC'}
    t35 = bars['A'].index[n - 41]
    seq = _hot_since_by_day(bars, 5)
    assert {s for s, _, _ in seq} == {t35}
    assert not any(b for _, b, _ in seq)                    # 이력 안에서 시작일을 찾았으니 '모름' 아님
    assert all(last == s for s, _, last in seq)
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1], min_aligned=0)['OPT']
    assert h['fires'] == []                                 # 창 안 🔥 시작일 목록은 그대로 창 기준
    assert h['daysAgo'] == 40


def test_hot_sectors_이력_처음까지_이어진_불은_시작일_모름():
    # 렐볼 판정이 가능한 첫날(20일 평균 + 5일 창 = 25번째 봉)부터 8거래일마다 켜짐 → 시작일을 알 수 없다
    n = 60
    bars = {t: _bars(n, (36, 28, 20, 12, 4)) for t in 'ABC'}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1], min_aligned=0)['OPT']
    assert h['startedBefore'] and h['last'] is None
    assert h['since'] == bars['A'].index[n - 36]            # 알 수 있는 이력 안에서 처음 켜진 날


def test_bull_since_최근5일_첫_렐볼양봉():
    bars = {'A': _bars(80, (4, 2)), 'B': _bars(80, (9,))}
    out = nl.bull_since(bars, bars['A'].index[-1])
    assert out == {'A': bars['A'].index[-4]}

def test_hot_sectors_정배열_비율_낮으면_제외():
    bars = {t: _bars(80, (1,)) for t in 'ABC'}
    for df in bars.values():                             # 꾸준히 내려온 종목 — 정배열 0%
        df['Close'] = np.linspace(20, 10, len(df))
        df['Open'] = df['Close'] + .2
        df.iloc[-1, df.columns.get_loc('Open')] = 9.9    # 마지막 날만 렐볼 양봉 (거래량 300)
    assert nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1]) == {}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1], min_aligned=0)
    assert h['OPT']['alignedShare'] == 0

# ── #41 M6: 기준일 봉 결측·늦은 상장 종목의 차기 주도주 점수 ─────────────────
def _rebound_setup():
    """조정(기산점 → 바닥) 뒤 찐반등. 반등 구간 시작일(찐반등 전날) 종가가 그 전날과 같게 평평하게 둔다."""
    closes = list(np.linspace(100, 130, 100)) + list(np.linspace(129, 100, 15)) + [100.0] + list(np.linspace(103, 110, 5))
    idf = _index(closes)
    peak, base, jjin = idf['Close'].idxmax(), idf.index[115], idf.index[116]
    phase = {'mode': 'rebound', 'scoring': 'rebound', 'correctionStart': peak, 'peakDate': peak, 'jjinDate': jjin,
             'jjinBase': base, 'indexSinceJjin': 100 * (idf['Close'].iloc[-1] / idf.loc[base, 'Close'] - 1)}
    return idf, phase


def _stock(idf, mult):
    s = idf.copy()
    for c in ('Open', 'High', 'Low', 'Close'):
        s[c] = s[c] * mult
    return s


def _rows(*tickers):
    return [{'ticker': t, 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': True, 'hasBullishRvol15InLast5': False}
            for t in tickers]


def test_반등_시작일_봉이_없어도_같은_가격이면_같은_점수():
    # TWIN·GAP은 가격이 같고 GAP만 찐반등 전날 봉이 빠졌다 — 빠진 날은 직전 거래일 종가로 맞춘다 (#41 M6)
    idf, phase = _rebound_setup()
    twin = _stock(idf, 1.0)
    twin.iloc[-5:, twin.columns.get_loc('Close')] *= 1.05            # 반등 세기를 지수와 다르게
    gap = twin.drop(index=phase['jjinBase'])
    other = _stock(idf, 1.0)
    bars = {'TWIN': twin, 'GAP': gap, 'OTHER': other}
    out = {r['ticker']: r for r in nl.score_stocks(_rows(*bars), bars, idf, {t: 'X' for t in bars}, {'X'},
                                                   phase, idf.index[-1])}
    assert out['GAP']['sinceJjinExcess'] == pytest.approx(out['TWIN']['sinceJjinExcess'])
    assert out['GAP']['nextScore'] == pytest.approx(out['TWIN']['nextScore'])


def test_기산점_뒤에_상장한_종목은_덜빠짐을_빼고_재정규화한다():
    idf, phase = _rebound_setup()
    ipo = _stock(idf, 1.0).iloc[108:]                     # 기산점(100번째 봉 근처) 뒤 상장
    bars = {'A': _stock(idf, 1.0), 'B': _stock(idf, 2.0), 'IPO': ipo}
    bars['B'].iloc[-5:, bars['B'].columns.get_loc('Close')] *= 0.97
    out = {r['ticker']: r for r in nl.score_stocks(_rows(*bars), bars, idf, {t: 'X' for t in bars}, {'X'},
                                                   phase, idf.index[-1])}
    r = out['IPO']
    assert r['sincePeakExcessAdr'] is None
    assert r['nextParts']['덜 빠짐'] is None                # 0점이 아니라 '계산 불가'
    w = nl.WEIGHTS['rebound']
    used = {k: v for k, v in r['nextParts'].items() if v is not None}
    assert r['nextScore'] == pytest.approx(100 * sum(used.values()) / sum(w[k] for k in used))
    # 다 계산되는 종목은 그대로 배점 합
    assert out['A']['nextScore'] == pytest.approx(sum(out['A']['nextParts'].values()))


# ── #41 M9: 급부상 종목 관문 — 순수 함수 hot_stock_pool ─────────────────
def _prow(t, theme, pp=60.0, adr=8.0, bull5=True, **kw):
    """rotation_scores 결과 행 (hot_stock_pool 입력)."""
    base = {'ticker': t, 'primaryTheme': theme, 'ppScore': pp, 'adrNow': adr, 'hasBullishRvol15InLast5': bull5,
            'belowSma50': False, 'belowSma150': False, 'belowSma200': False}
    base.update(kw)
    return base


def _hot(since, now=True, started_before=False):
    return {'since': since, 'startedBefore': started_before, 'now': now, 'last': None if started_before else since}


def _pool(rows, hot, bars, sizes=None, bull=None, asof=None, **kw):
    asof = asof or next(iter(bars.values())).index[-1]
    sizes = sizes if sizes is not None else {r['primaryTheme']: 3 for r in rows}
    return nl.hot_stock_pool(rows, hot, sizes, bull or {}, bars, asof, **kw)


def test_hot_stock_pool_불_섹터_종목만_PP점수순():
    bars = {t: _bars(80, (2,)) for t in ('A', 'B', 'C')}
    idx = bars['A'].index
    rows = [_prow('A', 'OPT', 60), _prow('B', 'OPT', 80), _prow('C', 'COLD', 90)]
    out = _pool(rows, {'OPT': _hot(idx[-3])}, bars)
    assert [r['ticker'] for r in out['rows']] == ['B', 'A']


def test_hot_stock_pool_ADR_6퍼센트_경계():
    bars = {t: _bars(80, (2,)) for t in ('EQ', 'LO')}
    idx = bars['EQ'].index
    rows = [_prow('EQ', 'OPT', adr=6.0), _prow('LO', 'OPT', adr=5.99)]
    out = _pool(rows, {'OPT': _hot(idx[-3])}, bars)
    assert [r['ticker'] for r in out['rows']] == ['EQ']          # 6.0%는 통과 (pp_prefilter도 6 미만만 제외)


def test_hot_stock_pool_편입_후_자기_렐볼_양봉이_없으면_제외():
    bars = {'OWN': _bars(80, (2,)), 'NONE': _bars(80, (30,))}     # NONE은 편입(5거래일 앞)보다 훨씬 전에만 렐볼
    idx = bars['OWN'].index
    out = _pool([_prow('OWN', 'OPT'), _prow('NONE', 'OPT')], {'OPT': _hot(idx[-3])}, bars)
    assert [r['ticker'] for r in out['rows']] == ['OWN']


def _dd_bars(last):
    """편입 뒤 종가 고점 128 → 마지막 종가 last. 편입일(뒤에서 10번째) 날 렐볼 양봉."""
    df = _bars(80, (10,))
    df.iloc[-10, df.columns.get_loc('Close')] = 128.0
    df.iloc[-10, df.columns.get_loc('Open')] = 120.0
    df.iloc[-9:, df.columns.get_loc('Close')] = 100.0
    df.iloc[-1, df.columns.get_loc('Close')] = last
    return df


def test_hot_stock_pool_고점에서_정확히_3ADR_밀리면_탈락():
    # (80/128 − 1) × 100 / 12.5 = −3.0 정확히 → '3 ADR 이상' 탈락. 조금 덜 밀리면 남는다
    bars = {'EQ': _dd_bars(80.0), 'IN': _dd_bars(80.5)}
    idx = bars['EQ'].index
    rows = [_prow('EQ', 'OPT', adr=12.5), _prow('IN', 'OPT', adr=12.5)]
    out = _pool(rows, {'OPT': _hot(idx[-10])}, bars)
    assert [r['ticker'] for r in out['rows']] == ['IN']
    assert out['rows'][0]['ddAdr'] == pytest.approx(100 * (80.5 / 128 - 1) / 12.5)


def test_hot_stock_pool_50_150_200일선_아래는_탈락():
    bars = {t: _bars(80, (2,)) for t in ('OK', 'S50', 'S150', 'S200')}
    idx = bars['OK'].index
    rows = [_prow('OK', 'OPT'), _prow('S50', 'OPT', belowSma50=True), _prow('S150', 'OPT', belowSma150=True),
            _prow('S200', 'OPT', belowSma200=True)]
    out = _pool(rows, {'OPT': _hot(idx[-3])}, bars)
    assert [r['ticker'] for r in out['rows']] == ['OK']


def test_hot_stock_pool_50점_경계는_weak_아님():
    bars = {t: _bars(80, (2,)) for t in ('EQ', 'LO')}
    idx = bars['EQ'].index
    out = _pool([_prow('EQ', 'OPT', 50.0), _prow('LO', 'OPT', 49.99)], {'OPT': _hot(idx[-3])}, bars)
    weak = {r['ticker']: r['weak'] for r in out['rows']}
    assert weak == {'EQ': False, 'LO': True}                 # 50점 미만만 기본 숨김 (토글로 보기)


def test_hot_stock_pool_NEW는_편입일이_오늘이고_시작일을_알_때만():
    bars = {t: _bars(80, (1,)) for t in ('A',)}
    idx = bars['A'].index
    assert _pool([_prow('A', 'OPT')], {'OPT': _hot(idx[-1])}, bars)['rows'][0]['isNew']
    assert not _pool([_prow('A', 'OPT')], {'OPT': _hot(idx[-2])}, bars)['rows'][0]['isNew']
    assert not _pool([_prow('A', 'OPT')], {'OPT': _hot(idx[-1], started_before=True)}, bars)['rows'][0]['isNew']


def test_hot_stock_pool_섹터_종목이_3개_미만이면_단독_렐볼로_포함():
    bars = {t: _bars(80, (2,)) for t in ('SOLO', 'QUIET', 'BIG')}
    idx = bars['SOLO'].index
    rows = [_prow('SOLO', 'TINY'), _prow('QUIET', 'TINY', bull5=False), _prow('BIG', 'LARGE')]
    out = _pool(rows, {}, bars, sizes={'TINY': 2, 'LARGE': 3}, bull={'SOLO': idx[-2]})
    assert [(r['ticker'], r['solo']) for r in out['rows']] == [('SOLO', True)]
    assert out['rows'][0]['entry'] == idx[-2]


def test_hot_stock_pool_입력_행을_바꾸지_않는다():
    bars = {'A': _bars(80, (2,))}
    rows = [_prow('A', 'OPT')]
    _pool(rows, {'OPT': _hot(bars['A'].index[-3])}, bars)
    assert 'ddAdr' not in rows[0]                             # 캐시된 rotStocks를 오염시키지 않게


def _long_ma_df(last):
    """100(110봉) → 200(90봉) → 100에서 last까지 상승(60봉). ADR 7%, 최근 정배열.
    SMA150 ≈ 172, SMA200 ≈ 154 근처라 last 160은 150일선 아래·200일선 위."""
    closes = np.array([100.0] * 110 + [200.0] * 90 + list(np.linspace(100, last, 60)))
    idx = pd.bdate_range(end='2026-11-06', periods=len(closes))
    return pd.DataFrame({'Open': closes, 'High': closes * 1.04, 'Low': closes * 0.97, 'Close': closes,
                         'Volume': np.full(len(closes), 1e6)}, index=idx)


def test_150일선_관문은_pp_prefilter와_같은_정의():
    from strategy.pivot_candle import pp_prefilter
    below150 = _long_ma_df(160.0)
    assert nl.below_sma(below150, 150) and not nl.below_sma(below150, 200)
    assert pp_prefilter(below150) is None                  # 10EMA 탭도 제외
    above = _long_ma_df(185.0)
    assert not nl.below_sma(above, 150) and not nl.below_sma(above, 200)
    assert pp_prefilter(above) is not None


def test_장기_이평_관문_경계_이평과_같으면_통과_이력_부족도_통과():
    flat = _index(np.full(220, 100.0))                     # 종가 = 200일선 (gap200 = 0)
    assert nl.sma200_gap(flat) == pytest.approx(0.0)
    assert not nl.below_sma(flat, 200) and not nl.below_sma(flat, 150)
    short = _index(np.linspace(100, 90, 120))               # 150·200일 이력 없음 (gap200 None) — pp_prefilter와 같이 통과
    assert nl.sma200_gap(short) is None
    assert not nl.below_sma(short, 150) and not nl.below_sma(short, 200)


def test_rotation_scores가_150_200일선_아래를_표시한다():
    idf = _index(np.full(260, 100.0))
    bars = {'B150': _long_ma_df(160.0)}
    bars['B150'].index = idf.index
    rows = [{'ticker': 'B150', 'status': 'FULL', 'aboveEma10': True, 'aboveEma21': True, 'ema10DaysOfLast5': 5,
             'hasBullishRvol15InLast5': False}]
    x = nl.rotation_scores(rows, bars, idf, {'B150': 'X'}, {'X'}, idf.index[-1])[0]
    assert x['belowSma150'] and not x['belowSma200'] and not x['belowSma50']


def test_렐볼_양봉_정의는_sector_strength_하나():
    # 직전 20일 거래량 평균이 0(거래정지 뒤 재개 등)이면 RVOL을 낼 수 없다 — 명세 RVOL20과 같이 렐볼 아님.
    # 예전 hot_sectors·bull_since·UI는 '거래량 ≥ 0 × 1.5'로 참이 되어 정의가 갈렸다.
    df = _bars(60, (1,))
    df.iloc[-21:-1, df.columns.get_loc('Volume')] = 0.0
    assert not ss.bullish_rvol(df).iloc[-1]
    assert nl.bull_since({'A': df}, df.index[-1]) == {}
    normal = _bars(60, (1,))
    assert ss.bullish_rvol(normal).iloc[-1]
    assert nl.bull_since({'A': normal}, normal.index[-1]) == {'A': normal.index[-1]}
    assert nl.HOT_RVOL == ss.RVOL_BULL


# ── #41 M7: 🔥 판정 분모 = 섹터 크기 고정, 결측 = 렐볼 아님, 그룹 크기 단일 출처 ─────────────────
def test_hot_sectors_한_종목만_당일_봉이_없어도_분모는_섹터_크기():
    # A·B 렐볼 양봉, C만 기준일 봉 없음 → 2/3 = 67% 🔥 (예전엔 분모가 2로 줄어 min_n 미만 → share 0, 🔥 꺼짐)
    bars = {'A': _bars(80, (1,)), 'B': _bars(80, (1,)), 'C': _bars(80).iloc[:-1]}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1], min_aligned=0)
    assert h['OPT']['now'] and h['OPT']['share'] == pytest.approx(2 / 3)
    assert h['OPT']['n'] == 3


def test_hot_sectors_이력_짧은_종목도_섹터_크기에_들어가고_렐볼_아님으로_센다():
    # C는 상장 10거래일 — 예전엔 30봉 미만이라 그룹에서 빠져 2종목 섹터(판정 불가)가 됐다
    bars = {'A': _bars(80, (1,)), 'B': _bars(80, (1,)), 'C': _bars(80).iloc[-10:]}
    theme = {t: 'OPT' for t in bars}
    assert {th: len(ts) for th, ts in nl.hot_groups(bars, theme, bars['A'].index[-1]).items()} == {'OPT': 3}
    h = nl.hot_sectors(bars, theme, bars['A'].index[-1], min_aligned=0)
    assert h['OPT']['now'] and h['OPT']['share'] == pytest.approx(2 / 3) and h['OPT']['n'] == 3


def test_hot_groups_기준일_이전_봉이_없거나_미분류면_세지_않는다():
    bars = {'A': _bars(80), 'LATE': _bars(80).iloc[-3:], 'U': _bars(80)}
    asof = bars['A'].index[-5]                                  # LATE는 그 뒤 상장
    groups = nl.hot_groups(bars, {'A': 'X', 'LATE': 'X', 'U': 'UNCLASSIFIED', 'NODATA': 'X'}, asof)
    assert groups == {'X': ['A']}


def test_단독_렐볼_판정은_hot_groups와_같은_섹터_크기를_쓴다():
    # 분류상 3종목이지만 한 종목은 시세가 없어 🔥 판정 그룹은 2종목 → 판정 불가 섹터의 렐볼 종목은 단독 렐볼로 잡는다
    bars = {'A': _bars(80, (2,)), 'B': _bars(80)}
    theme = {'A': 'X', 'B': 'X', 'NODATA': 'X'}
    asof = bars['A'].index[-1]
    sizes = {th: len(ts) for th, ts in nl.hot_groups(bars, theme, asof).items()}
    assert nl.hot_sectors(bars, theme, asof, min_aligned=0) == {}
    out = nl.hot_stock_pool([_prow('A', 'X')], {}, sizes, nl.bull_since(bars, asof), bars, asof)
    assert [(r['ticker'], r['solo']) for r in out['rows']] == [('A', True)]


# ── #41 M8: 정배열 20% 필터는 🔥가 켜진 날(🔥 구간 시작일) 기준 ─────────────────
def _path_bars(closes, bull_idx=()):
    """종가 경로 + bull_idx 위치만 거래량 3배 양봉 (나머지는 음봉·거래량 100)."""
    c = np.asarray(closes, dtype=float)
    idx = pd.bdate_range('2026-01-01', periods=len(c))
    o = c + 0.1
    v = np.full(len(c), 100.0)
    for i in bull_idx:
        o[i] = c[i] - 0.1
        v[i] = 300.0
    return pd.DataFrame({'Open': o, 'High': np.maximum(o, c) + .1, 'Low': np.minimum(o, c) - .1,
                         'Close': c, 'Volume': v}, index=idx)


def test_hot_sectors_정배열일_때_켜진_불은_눌림으로_정배열이_풀려도_이력에_남는다():
    # 꾸준히 오르다(정배열) 60번째 봉에 🔥 → 이후 하락으로 오늘은 정배열 0%. 예전엔 오늘 비율로 이력째 지웠다
    closes = list(np.linspace(10, 20, 62)) + list(np.linspace(19.5, 12, 18))
    bars = {t: _path_bars(closes, (60,)) for t in 'ABC'}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1])
    assert 'OPT' in h
    assert h['OPT']['since'] == bars['A'].index[60] and not h['OPT']['now']
    assert h['OPT']['alignedShare'] == 0                     # 오늘 비율(표시용)
    assert h['OPT']['alignedAtStart'] == 1.0                 # 🔥 시작일 비율(판정용)


def test_hot_sectors_정배열_아닐_때_켜진_불은_오늘_정배열이어도_제외():
    # 하락 중(정배열 아님) 60번째 봉에 🔥 → 이후 급반등으로 오늘은 정배열 100%. 시작일 기준이라 제외
    closes = list(np.linspace(20, 10, 60)) + list(np.linspace(10.5, 30, 20))
    bars = {t: _path_bars(closes, (60,)) for t in 'ABC'}
    asof = bars['A'].index[-1]
    assert nl.hot_sectors(bars, {t: 'OPT' for t in bars}, asof) == {}
    assert nl.hot_sectors(bars, {t: 'OPT' for t in bars}, asof, min_aligned=0)['OPT']['alignedShare'] == 1.0


def test_hot_sectors_정배열_미달로_새로_켜진_불은_무시하고_앞선_불을_유지():
    # 52번째 봉 🔥(정배열) → 하락 → 77번째 봉 재점화(정배열 아님, HOT_GAP 넘는 공백 뒤 새 🔥)
    closes = list(np.linspace(10, 20, 55)) + list(np.linspace(19.5, 12, 25))
    bars = {t: _path_bars(closes, (52, 77)) for t in 'ABC'}
    h = nl.hot_sectors(bars, {t: 'OPT' for t in bars}, bars['A'].index[-1])['OPT']
    assert h['share'] == 1.0 and not h['now']               # 오늘 렐볼 비율은 높지만 무효인 🔥
    assert h['since'] == bars['A'].index[52] and h['daysAgo'] == 27
    assert h['fires'] == [bars['A'].index[52]]


# ── #41 L11: 비메인 테마(지주·기타 등)는 급부상 판정에서 제외 — 차기 주도섹터(rank_sectors)와 같은 기준 ─────────
def test_hot_sectors_비메인_테마는_불이어도_제외():
    bars = {t: _bars(80, (1,)) for t in ('A', 'B', 'C', 'D', 'E', 'F')}
    theme = {'A': 'MAIN', 'B': 'MAIN', 'C': 'MAIN', 'D': 'HOLDING_OTHER', 'E': 'HOLDING_OTHER', 'F': 'HOLDING_OTHER'}
    asof = bars['A'].index[-1]
    assert set(nl.hot_sectors(bars, theme, asof, min_aligned=0)) == {'MAIN', 'HOLDING_OTHER'}     # 거르지 않으면 둘 다
    assert set(nl.hot_sectors(bars, theme, asof, min_aligned=0, main_codes={'MAIN'})) == {'MAIN'}


def test_hot_stock_pool_비메인_테마_종목은_단독_렐볼로도_넣지_않는다():
    bars = {t: _bars(80, (2,)) for t in ('M', 'H')}
    idx = bars['M'].index
    rows = [_prow('M', 'TINY'), _prow('H', 'HOLDING_OTHER')]
    out = _pool(rows, {}, bars, sizes={'TINY': 1, 'HOLDING_OTHER': 1}, bull={'M': idx[-2], 'H': idx[-2]},
                main_codes={'TINY'})
    assert [r['ticker'] for r in out['rows']] == ['M']


# ── #42 L9: 급부상 종목이 비었을 때 원인 구분 ─────────────────
def test_급부상_종목_안내_후보_자체가_없을_때():
    bars = {'A': _bars(80)}
    hp = _pool([_prow('A', 'OPT', bull5=False)], {}, bars, sizes={'OPT': 3})
    assert hp['candidates'] == 0
    assert nl.hot_pool_message(hp) == '최근 6주 안에 🔥 급부상 섹터도, 단독 급등 종목도 없습니다.'


def test_급부상_종목_안내_불_섹터는_있는데_점수_낼_종목이_없을_때():
    bars = {'A': _bars(80, (2,))}
    hp = _pool([], {'OPT': _hot(bars['A'].index[-3])}, bars)
    msg = nl.hot_pool_message(hp)
    assert '🔥 급부상 섹터는 1개 있지만' in msg and '단독 급등 종목도 없습니다' not in msg


def test_급부상_종목_안내_후보가_탈락_규칙으로_모두_빠졌을_때():
    bars = {t: _bars(80, (2,)) for t in ('LOW', 'S50', 'S200')}
    idx = bars['LOW'].index
    rows = [_prow('LOW', 'OPT', adr=5.0), _prow('S50', 'OPT', belowSma50=True), _prow('S200', 'OPT', belowSma200=True)]
    hp = _pool(rows, {'OPT': _hot(idx[-3])}, bars)
    assert hp['rows'] == [] and hp['candidates'] == 3
    assert hp['dropped'] == {'adr': 1, 'ownBull': 0, 'drawdown': 0, 'sma50': 1, 'sma150_200': 1}
    msg = nl.hot_pool_message(hp)
    assert msg.startswith('후보 3종목(🔥 섹터 3')
    assert 'ADR 6% 미만 1' in msg and '50일선 아래 1' in msg and '150·200일선 아래 1' in msg
    assert '자기 렐볼' not in msg                              # 0건 사유는 적지 않는다
    assert '단독 급등 종목도 없습니다' not in msg


def test_급부상_종목_안내_50점_미만만_남았을_때():
    bars = {'A': _bars(80, (2,))}
    hp = _pool([_prow('A', 'OPT', 40.0)], {'OPT': _hot(bars['A'].index[-3])}, bars)
    assert nl.hot_pool_message(hp, show_weak=True) is None
    assert '50점 미만' in nl.hot_pool_message(hp, show_weak=False)
