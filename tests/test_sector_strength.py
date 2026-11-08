"""마스터 명세 11장 '반드시 통과해야 할 테스트'."""
import numpy as np
import pandas as pd
import pytest

from strategy import sector_strength as ss

ASOF = pd.Timestamp('2026-11-06')


def make_df(closes, adr=4.0, opens=None, volumes=None, end=ASOF):
    """High/Low를 Low 대비 adr% 폭으로 만든 일봉. 기본은 양봉(open = close*0.99)."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    idx = pd.bdate_range(end=end, periods=n)
    low = closes / (1 + adr / 200)
    high = low * (1 + adr / 100)
    opens = np.asarray(opens, dtype=float) if opens is not None else closes * 0.99
    low = np.minimum(low, np.minimum(opens, closes))
    high = np.maximum(high, np.maximum(opens, closes))
    low = np.minimum(low, high / (1 + adr / 100))   # 폭을 정확히 adr%로 고정
    high = low * (1 + adr / 100)
    vol = np.asarray(volumes, dtype=float) if volumes is not None else np.full(n, 2_000_000.0)
    return pd.DataFrame({'Open': opens, 'High': high, 'Low': low, 'Close': closes, 'Volume': vol}, index=idx)


def flat_then(last6, n=130, base=100.0):
    """앞은 base로 평평, 마지막 6개 종가 지정."""
    return [base] * (n - 6) + list(last6)


def bench_bars(r5=5.0, r21=5.0, n=130):
    """QQQ·IWM 모두 5일 r5%, 21일 r21% 상승."""
    closes = [100.0] * n
    closes[-1] = 100.0 * (1 + r5 / 100)
    closes[-22] = closes[-1] / (1 + r21 / 100)
    df = make_df(closes, adr=1.0)
    return {'QQQ': df, 'IWM': df.copy()}


def test_NRS_명세_예시_ADR2는_7_5_ADR8은_1_875():
    bars = bench_bars(r5=5.0)
    up20 = flat_then([100, 100, 100, 100, 100, 120])
    bars['LOWV'] = make_df(up20, adr=2.0)
    bars['HIGHV'] = make_df(up20, adr=8.0)
    bench = ss.build_bench(bars, ASOF)
    lo = ss.stock_metrics('LOWV', bars['LOWV'], bench)
    hi = ss.stock_metrics('HIGHV', bars['HIGHV'], bench)
    assert lo['rs5Blend'] == pytest.approx(15.0)
    assert lo['adr20Pct'] == pytest.approx(2.0)
    assert lo['nrs5'] == pytest.approx(7.5)
    assert hi['nrs5'] == pytest.approx(1.875)


def test_RS는_QQQ_IWM_각각과_평균을_모두_계산한다():
    bars = bench_bars(r5=5.0)
    bars['IWM'] = make_df(flat_then([100, 100, 100, 100, 100, 101]), adr=1.0)
    bars['X'] = make_df(flat_then([100, 100, 100, 100, 100, 110]))
    bench = ss.build_bench(bars, ASOF)
    m = ss.stock_metrics('X', bars['X'], bench)
    assert m['rs5B1'] == pytest.approx(5.0)       # vs QQQ
    assert m['rs5B2'] == pytest.approx(9.0)       # vs IWM
    assert m['rs5Blend'] == pytest.approx(7.0)


def test_KR은_KOSPI_KOSDAQ을_벤치마크로_쓰고_거래대금으로_유동성_판정():
    up = make_df(flat_then([100, 100, 100, 100, 100, 102]), adr=1.0)
    flat = make_df(flat_then([100] * 6), adr=1.0)
    bars = {'KOSPI': up, 'KOSDAQ': flat,
            'A': make_df(flat_then([100, 100, 100, 100, 100, 110]), volumes=[10_000.0] * 130)}
    rep = ss.run(bars, ['A', 'KOSPI'], {'A': 'SHIPBUILDING'}, {'SHIPBUILDING'}, ASOF,
                 bench_names=('KOSPI', 'KOSDAQ'), liquidity_shares=None, liquidity_value=5e9)
    a = rep.stocks[0]
    assert [r['ticker'] for r in rep.stocks] == ['A']
    assert a['rs5B1'] == pytest.approx(8.0) and a['rs5B2'] == pytest.approx(10.0)
    assert a['rs5Blend'] == pytest.approx(9.0)
    assert a['liquidityOk'] is False              # 1만주 × 약 100원 = 100만원 < 50억


def test_벤치마크가_하나면_그_지수만_기준이다():
    up = make_df(flat_then([100, 100, 100, 100, 100, 102]), adr=1.0)
    flat = make_df(flat_then([100] * 6), adr=1.0)
    bars = {'KOSPI': up, 'KOSDAQ': flat,
            'A': make_df(flat_then([100, 100, 100, 100, 100, 110]))}
    rep = ss.run(bars, ['A'], {'A': 'SHIPBUILDING'}, {'SHIPBUILDING'}, ASOF, bench_names=('KOSPI',))
    a = rep.stocks[0]
    assert rep.bench.names == ('KOSPI',)
    assert a['rs5Blend'] == pytest.approx(8.0)    # KOSDAQ(0%)이 섞이지 않는다
    assert a['rs5B1'] == a['rs5B2'] == pytest.approx(8.0)
    del bars['KOSDAQ']                            # 다른 지수가 없어도 계산된다
    assert ss.run(bars, ['A'], {'A': 'SHIPBUILDING'}, {'SHIPBUILDING'}, ASOF,
                  bench_names=('KOSPI',)).stocks[0]['status'] == 'FULL'


def test_수익률은_오늘종가_나누기_N거래일전_종가():
    closes = list(range(100, 230))       # 매일 +1
    bars = bench_bars()
    bars['X'] = make_df(closes)
    bench = ss.build_bench(bars, ASOF)
    m = ss.stock_metrics('X', bars['X'], bench)
    assert m['ret5'] == pytest.approx((229 / 224 - 1) * 100)
    assert m['ret21'] == pytest.approx((229 / 208 - 1) * 100)


def test_벤치마크는_유니버스_점수에_포함되지_않는다():
    bars = bench_bars()
    bars['X'] = make_df(flat_then([100] * 6))
    rep = ss.run(bars, ['QQQ', 'IWM', 'X'], {'X': 'SEMI_EQUIPMENT'}, {'SEMI_EQUIPMENT'}, ASOF)
    assert [r['ticker'] for r in rep.stocks] == ['X']
    assert rep.coverage['total'] == 1


def test_EMA10_유지는_각_거래일의_EMA와_비교한다():
    # 월~목 소폭 상승(그날 EMA 위), 금요일 급등으로 금요일 EMA가 월~목 종가보다 높아진다.
    closes = [100.0] * 125 + [101, 102, 103, 104, 200]
    bars = bench_bars()
    bars['X'] = make_df(closes)
    bench = ss.build_bench(bars, ASOF)
    m = ss.stock_metrics('X', bars['X'], bench)
    c = bars['X']['Close']
    e = c.ewm(span=10, adjust=False).mean()
    friday_reuse = int((c.iloc[-5:] > e.iloc[-1]).sum())
    assert m['ema10DaysOfLast5'] == 5
    assert friday_reuse == 1            # 금요일 EMA 재사용 방식이면 1이 나온다


def test_RVOL20_분모에_당일_거래량을_넣지_않는다():
    vol = [1_000_000.0] * 129 + [3_000_000.0]
    bars = bench_bars()
    bars['X'] = make_df(flat_then([100] * 6), volumes=vol)
    bench = ss.build_bench(bars, ASOF)
    m = ss.stock_metrics('X', bars['X'], bench)
    assert m['rvol20'] == pytest.approx(3.0)            # 포함했다면 3/1.1 = 2.727


def test_양봉은_종가_시가_비교다_전일대비_상승이_아니다():
    # 마지막 날: 전일보다 올랐지만(105>100) 시가 110보다 낮은 음봉 + 거래량 3배
    closes = flat_then([100, 100, 100, 100, 100, 105])
    opens = [c * 0.99 for c in closes]
    opens[-1] = 110
    vol = [1_000_000.0] * 129 + [3_000_000.0]
    opens[-6:-1] = [101] * 5                             # 앞 4일도 음봉(시가>종가)
    bars = bench_bars()
    bars['X'] = make_df(closes, opens=opens, volumes=vol)
    bench = ss.build_bench(bars, ASOF)
    m = ss.stock_metrics('X', bars['X'], bench)
    assert m['hasBullishRvol15InLast5'] is False


def test_데이터가_11개뿐이면_AB_판정불가_0점이_아니다():
    bars = bench_bars()
    theme_of, universe = {}, []
    for i in range(210):
        t = f'S{i}'
        universe.append(t)
        theme_of[t] = 'SEMI_EQUIPMENT' if i < 105 else 'OPTICAL_INTERCONNECT'
        if i < 11:
            bars[t] = make_df(flat_then([100, 101, 102, 103, 104, 130]))
    rep = ss.run(bars, universe, theme_of, {'SEMI_EQUIPMENT', 'OPTICAL_INTERCONNECT'}, ASOF)
    assert rep.coverage['full'] == 11
    for s in rep.sectors:
        assert s['grade'] == 'UNVERIFIED'
        assert s['score'] is None
    assert rep.coverage['byStatus']['MISSING'] == 199


def test_미분류와_외부종목은_섹터를_부풀리지_않는다():
    bars = bench_bars()
    uni, theme_of = [], {}
    for i in range(5):
        t = f'Q{i}'
        uni.append(t)
        theme_of[t] = 'QUANTUM_COMPUTING'
        bars[t] = make_df(flat_then([100] * 6))
    bars['APP'] = make_df(flat_then([100] * 5 + [170]))      # 외부 발견 종목 — 유니버스에 없음
    theme_of['APP'] = 'QUANTUM_COMPUTING'
    bars['AMBI'] = make_df(flat_then([100] * 5 + [170]))     # 원본이지만 미분류
    uni.append('AMBI')
    rep = ss.run(bars, uni, theme_of, {'QUANTUM_COMPUTING'}, ASOF)
    q = next(s for s in rep.sectors if s['theme'] == 'QUANTUM_COMPUTING')
    assert q['memberCount'] == 5
    assert 'APP' not in q['members'] and 'AMBI' not in q['members']
    assert q['medianRet5'] == pytest.approx(0.0)


def test_기준일_이후_데이터는_쓰지_않는다():
    bars = bench_bars()
    future_end = ASOF + pd.Timedelta(days=3)               # 11/09(월)까지 있는 데이터
    closes = [100.0] * 130 + [500.0]
    df = make_df(closes, end=future_end)
    bars['X'] = df
    bench = ss.build_bench(bars, ASOF)
    m = ss.stock_metrics('X', df, bench)
    assert m['lastDate'] == '2026-11-06'
    assert m['close'] == pytest.approx(100.0)


def test_대장주_한개_40퍼센트는_섹터_중앙값을_40으로_만들지_않는다():
    bars = bench_bars()
    uni, theme_of = [], {}
    for i in range(6):
        t = f'O{i}'
        uni.append(t)
        theme_of[t] = 'OPTICAL_INTERCONNECT'
        last = 140 if i == 0 else 101
        bars[t] = make_df(flat_then([100] * 5 + [last]))
    rep = ss.run(bars, uni, theme_of, {'OPTICAL_INTERCONNECT'}, ASOF)
    s = rep.sectors[0]
    assert s['medianRet5'] == pytest.approx(1.0)


def test_백분위는_최하위_0_최상위_1_동점은_평균순위():
    p = ss.percentile_rank({'a': 1.0, 'b': 2.0, 'c': 2.0, 'd': 5.0, 'e': None})
    assert p['a'] == 0.0 and p['d'] == 1.0
    assert p['b'] == p['c'] == pytest.approx(0.5)
    assert p['e'] is None
    assert ss.percentile_rank({'x': 3.0}) == {'x': None}


def test_결측_종목은_STALE_MISSING으로_표시하고_값을_만들지_않는다():
    bars = bench_bars()
    bars['OLD'] = make_df(flat_then([100] * 6), end=ASOF - pd.Timedelta(days=1))
    bench = ss.build_bench(bars, ASOF)
    assert ss.stock_metrics('OLD', bars['OLD'], bench)['status'] == 'STALE'
    m = ss.stock_metrics('NONE', None, bench)
    assert m['status'] == 'MISSING' and 'ret5' not in m


def test_거래일_누락은_INVALID_DATA():
    bars = bench_bars()
    df = make_df(flat_then([100] * 6))
    bars['GAP'] = df.drop(df.index[-3])
    bench = ss.build_bench(bars, ASOF)
    assert ss.stock_metrics('GAP', bars['GAP'], bench)['status'] == 'INVALID_DATA'


def test_벤치마크에_기준일_종가가_없으면_계산을_거부한다():
    bars = bench_bars()
    bars['IWM'] = bars['IWM'].iloc[:-1]
    with pytest.raises(ValueError, match='IWM'):
        ss.build_bench(bars, ASOF)


def test_A_등급은_점수와_게이트를_모두_통과해야_한다():
    bars = bench_bars(r5=1.0, r21=1.0)
    uni, theme_of = [], {}
    specs = {
        'STRONG': ('OPTICAL_INTERCONNECT', list(np.linspace(100, 130, 17)) + [132, 134, 136, 138, 140]),
        'MID': ('SOFTWARE_AI_DATA', list(np.linspace(100, 105, 17)) + [105.5, 106, 106.5, 107, 107.5]),
        'WEAK': ('SEMI_EQUIPMENT', list(np.linspace(100, 90, 17)) + [89, 88, 87, 86, 85]),
    }
    for name, (code, tail) in specs.items():
        for i in range(5):
            t = f'{name}{i}'
            uni.append(t)
            theme_of[t] = code
            vol = [1_000_000.0] * 129 + [3_000_000.0 if name == 'STRONG' else 1_000_000.0]
            bars[t] = make_df([100.0] * 108 + tail, volumes=vol)
    rep = ss.run(bars, uni, theme_of, {c for c, _ in specs.values()}, ASOF)
    grades = {s['theme']: s for s in rep.sectors}
    strong = grades['OPTICAL_INTERCONNECT']
    assert strong['score'] == pytest.approx(100.0)
    assert strong['grade'] == 'A_CONFIRMED'
    assert grades['SEMI_EQUIPMENT']['grade'] != 'A_CONFIRMED'
    assert 0 <= grades['SEMI_EQUIPMENT']['score'] <= 100
