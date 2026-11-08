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


def test_관찰_후순위_구분():
    assert ss.grade_bucket({'grade': 'C_WATCH', 'score': 50}) == 'C_WATCH'
    assert ss.grade_bucket({'grade': 'C_WATCH', 'score': 44}) == 'D_LOW'
    assert ss.grade_bucket({'grade': 'B_EMERGING', 'score': 61}) == 'B_EMERGING'
