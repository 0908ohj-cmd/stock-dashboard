from datetime import date, datetime

import pandas as pd
import pytest

from data import live_index as li


def _confirmed(dates, closes):
    return pd.DataFrame({'Close': closes}, index=pd.to_datetime(dates))


def _quote(day, price=5400.0):
    return li.LiveQuote(price=price, session_date=day,
                        fetched_at=datetime(2026, 11, 12, 10, 5, tzinfo=li.KST))


# ── display_values ───────────────────────────────────────

CONFIRMED = _confirmed(['2026-11-10', '2026-11-11'], [5239.13, 5375.36])


def test_live_session_newer_than_batch_uses_live_price_vs_batch_close():
    d = li.display_values(CONFIRMED, _quote(date(2026, 11, 12), 5400.0))

    assert d.live is True
    assert d.value == 5400.0
    assert d.delta_pct == pytest.approx((5400.0 / 5375.36 - 1) * 100)   # 배치 확정 종가 대비


def test_session_already_in_batch_uses_confirmed_values():
    """장 시작 전 yfinance는 직전 세션을 준다 — 배치에 있으면 확정값(pykrx)을 쓴다."""
    d = li.display_values(CONFIRMED, _quote(date(2026, 11, 11), 5375.36))

    assert d.live is False
    assert d.value == 5375.36
    assert d.delta_pct == pytest.approx((5375.36 / 5239.13 - 1) * 100)


def test_no_quote_falls_back_to_confirmed():
    d = li.display_values(CONFIRMED, None)
    assert d.live is False and d.value == 5375.36


def test_older_quote_never_overrides_batch():
    d = li.display_values(CONFIRMED, _quote(date(2026, 11, 10), 1.0))
    assert d.live is False and d.value == 5375.36


# ── fetch_live_quote ─────────────────────────────────────

class _FakeYF:
    def __init__(self, bar_times_utc, last_price, fail=False):
        self.bar_times = bar_times_utc
        self.last_price = last_price
        self.fail = fail

    def download(self, symbol, **kw):
        if self.fail:
            raise RuntimeError('network')
        idx = pd.DatetimeIndex(pd.to_datetime(self.bar_times), tz='UTC')
        return pd.DataFrame({'Close': [1.0] * len(idx)}, index=idx)

    def Ticker(self, symbol):
        price = self.last_price

        class _T:
            class fast_info:
                last_price = price
        return _T()


def test_kr_session_date_uses_seoul_time():
    """1분봉은 UTC로 온다 — KST 09:00~14:59 장은 UTC 00:00~05:59."""
    fake = _FakeYF(['2026-11-12 00:00', '2026-11-12 01:04'], 5401.2)
    q = li.fetch_live_quote('KOSPI', yf_module=fake)

    assert q.price == 5401.2
    assert q.session_date == date(2026, 11, 12)


def test_nasdaq_session_date_uses_new_york_time():
    """KST 11/12 05:59 마감 봉은 뉴욕 11/11 15:59 — 세션일은 11/11."""
    fake = _FakeYF(['2026-11-11 14:30', '2026-11-11 20:59'], 25466.19)
    q = li.fetch_live_quote('NASDAQ', yf_module=fake)

    assert q.session_date == date(2026, 11, 11)


@pytest.mark.parametrize('fake', [
    _FakeYF([], 5400.0),                          # 빈 응답
    _FakeYF(['2026-11-12 00:00'], 0.0),           # 현재가 없음
    _FakeYF(['2026-11-12 00:00'], float('nan')),
    _FakeYF(['2026-11-12 00:00'], 5400.0, fail=True),
])
def test_failures_return_none(fake):
    assert li.fetch_live_quote('KOSDAQ', yf_module=fake) is None
