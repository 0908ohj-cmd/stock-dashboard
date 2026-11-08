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
    # 수동 구간으로도 종목 점수가 계산돼야 한다 (자동 조정 시작일이 없을 때 ADR 기준 시점 보완)
    stock = _index(np.linspace(100, 170, 120))
    rows = [{'ticker': 'S', 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': True,
             'hasBullishRvol15InLast5': False}]
    for p in (ph, ph2):
        out = nl.score_stocks(rows, {'S': stock}, idf, {'S': 'X'}, {'X'}, p, '2026-11-06')
        assert out and out[0]['sincePeakExcessAdr'] is not None


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
    peak, jjin = idf['Close'].idxmax(), idf.index[115]
    phase = {'mode': 'rebound', 'correctionStart': peak, 'peakDate': peak, 'jjinDate': jjin,
             'indexSinceJjin': 100 * (idf['Close'].iloc[-1] / idf.loc[jjin, 'Close'] - 1)}
    rows = [{'ticker': 'S', 'status': 'FULL', 'adr20Pct': 2.0, 'aboveEma21': True, 'hasBullishRvol15InLast5': False}]
    r = nl.score_stocks(rows, {'S': stock}, idf, {'S': 'X'}, {'X'}, phase, '2026-11-06')[0]
    ret = lambda df, a, b: 100 * (df.loc[b, 'Close'] / df.loc[a, 'Close'] - 1)
    assert r['sincePeakExcess'] == pytest.approx(ret(stock, peak, jjin) - ret(idf, peak, jjin), abs=0.01)
    assert r['sinceJjinExcess'] == pytest.approx(ret(stock, jjin, idf.index[-1]) - ret(idf, jjin, idf.index[-1]))


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


def test_순환매_점수는_최근_가속과_거래량을_본다():
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
    assert out['LAG']['rotAccel'] > out['EARLY']['rotAccel']
    assert out['LAG']['rotScore'] > out['EARLY']['rotScore']


def test_관찰_후순위_구분():
    assert ss.grade_bucket({'grade': 'C_WATCH', 'score': 50}) == 'C_WATCH'
    assert ss.grade_bucket({'grade': 'C_WATCH', 'score': 44}) == 'D_LOW'
    assert ss.grade_bucket({'grade': 'B_EMERGING', 'score': 61}) == 'B_EMERGING'
