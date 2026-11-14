"""장중 오버레이 차트 — 날짜 경계선 위치 (#44 L2).

데이터는 tz가 붙은 시각이라 plotly가 현지 벽시계 시각으로 그리는데, 경계선은 UTC epoch ms로
넘겨 9시간(KR)·5시간(US) 어긋났다 — KR 선은 접힌 장외 구간에 숨고, US 선은 14:30에 그려졌다.
"""
import json

import numpy as np
import pandas as pd

from ui.intraday_overlay import intraday_overlay_chart


def _bars(days: list[str], open_time: str, tz: str, n: int) -> pd.DataFrame:
    """정규장 5분봉 — yfinance처럼 UTC로 바꿔 둔다(차트가 현지 시각으로 다시 바꾼다)."""
    idx = []
    for d in days:
        idx += list(pd.date_range(f'{d} {open_time}', periods=n, freq='5min', tz=tz))
    idx = pd.DatetimeIndex(idx).tz_convert('UTC')
    close = np.linspace(100, 101, len(idx))
    return pd.DataFrame({'Open': close, 'High': close, 'Low': close, 'Close': close, 'Volume': 1.0}, index=idx)


def _boundaries(fig) -> list[str]:
    """날짜 경계 수직선의 x — 직렬화된 값(plotly.js가 받는 값) 그대로."""
    shapes = json.loads(fig.to_json())['layout'].get('shapes', [])
    return [s['x0'] for s in shapes if s['type'] == 'line' and s.get('xref') == 'x']


def _wall(x) -> str:
    """plotly.js는 날짜 문자열의 tz 표기를 무시하고 벽시계 시각으로 그린다 — 'YYYY-MM-DD HH:MM'."""
    assert isinstance(x, str), f'경계선 x가 날짜가 아니라 숫자다(epoch ms): {x!r}'
    return x.replace('T', ' ')[:16]


def test_kr_boundaries_sit_at_0900_local_open():
    days = ['2026-11-09', '2026-11-10', '2026-11-11']
    df = _bars(days, '09:00', 'Asia/Seoul', 78)

    fig = intraday_overlay_chart(df, df, '005930', 'KOSPI', '2026-11-10', market='KR_KOSPI')

    assert [_wall(x) for x in _boundaries(fig)] == [f'{d} 09:00' for d in days]


def test_us_boundaries_sit_at_0930_local_open():
    days = ['2026-11-09', '2026-11-10']
    df = _bars(days, '09:30', 'America/New_York', 78)

    fig = intraday_overlay_chart(df, df, 'NVDA', 'NASDAQ', '2026-11-10', market='US')

    assert [_wall(x) for x in _boundaries(fig)] == [f'{d} 09:30' for d in days]


def test_boundaries_use_same_clock_as_data():
    """경계선과 그날 첫 봉이 같은 x(벽시계)에 놓인다."""
    days = ['2026-11-09', '2026-11-10']
    df = _bars(days, '09:30', 'America/New_York', 78)

    fig = intraday_overlay_chart(df, df, 'NVDA', 'NASDAQ', '2026-11-10', market='US')

    data_x = json.loads(fig.to_json())['data'][0]['x']
    first_bars = sorted({_wall(x) for x in data_x if _wall(x).endswith('09:30')})
    assert [_wall(x) for x in _boundaries(fig)] == first_bars
