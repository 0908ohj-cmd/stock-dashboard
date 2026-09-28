import pandas as pd

from scripts import build_kr_10ema_universe as bu


def _cap_df(rows):
    """pykrx get_market_cap_by_ticker 반환 형태 — index가 6자리 종목코드."""
    return pd.DataFrame(
        {
            '종가':     [r[1] for r in rows],
            '시가총액': [r[2] for r in rows],
            '거래량':   [r[3] for r in rows],
            '거래대금': [r[4] for r in rows],
        },
        index=[r[0] for r in rows],
    )


def test_pykrx_fallback_filters_by_marcap_and_amount(monkeypatch):
    """시총 1000억 미만, 거래대금 5억 미만은 1차 후보에서 빠진다."""
    df = _cap_df([
        ('005930', 10_000, 200_000_000_000, 1_000_000, 5_000_000_000),  # 통과
        ('000660', 20_000,  50_000_000_000, 1_000_000, 5_000_000_000),  # 시총 미달
        ('035720', 30_000, 300_000_000_000, 1_000_000,       100_000),  # 거래대금 미달
    ])
    calls = []

    def fake(date_str, market):
        calls.append((date_str, market))
        return df

    monkeypatch.setattr(bu, '_pykrx_market_cap', fake)

    out = bu._get_candidates_pykrx('KOSPI', '.KS')

    assert out == [('005930', '005930.KS')]
    assert len(calls) == 1          # 시장당 1회 — KRX 차단 방지
    assert calls[0][1] == 'KOSPI'


def test_pykrx_fallback_steps_back_when_empty(monkeypatch):
    """휴장일이라 빈 응답이 오면 앞 영업일로 한 칸 물러난다."""
    good = _cap_df([('005930', 10_000, 200_000_000_000, 1_000_000, 5_000_000_000)])
    seq = [pd.DataFrame(), good]
    monkeypatch.setattr(bu, '_pykrx_market_cap', lambda d, m: seq.pop(0))

    assert bu._get_candidates_pykrx('KOSPI', '.KS') == [('005930', '005930.KS')]


def test_build_market_falls_back_when_fdr_raises(monkeypatch):
    """FDR 404가 나도 스크립트가 죽지 않고 pykrx로 넘어간다."""
    def boom(*a, **k):
        raise RuntimeError('HTTP Error 404: Not Found')

    monkeypatch.setattr(bu, '_get_candidates_fdr', boom)
    monkeypatch.setattr(bu, '_get_candidates_pykrx',
                        lambda m, s: [('005930', '005930.KS')])
    monkeypatch.setattr(bu, '_filter_by_adr', lambda c: [('005930', 7.5)])

    assert bu.build_market('KOSPI', '.KS') == ['005930']


def test_recent_krx_dates_skips_weekend():
    """주말은 조회 없이 건너뛴다 — 불필요한 KRX 호출을 만들지 않는다."""
    dates = bu._recent_krx_dates(max_back=5)

    assert len(dates) == 5
    assert all(pd.Timestamp(d).weekday() < 5 for d in dates)
    assert dates == sorted(dates, reverse=True)   # 최신순
