"""스윙로우 다이버전스 등급 계산.

사용자가 지정한 N개 날짜(지수 저점)를 기준으로,
개별 종목이 동일 날짜에 고→고→고(상승 다이버전스) 인지
저→저→저(지수 동행) 인지 패턴화해 등급을 부여한다.
"""

import pandas as pd

# 등급 정렬 우선순위 (낮을수록 상단)
GRADE_ORDER = {'S': 0, 'A+': 1, 'A': 2, 'A-': 3, 'B': 4, 'C': 5, '—': 6}


def _close_on(df: pd.DataFrame, date) -> float | None:
    """date 당일 또는 그 이전 가장 가까운 거래일 종가."""
    avail = df[df.index <= pd.Timestamp(date)]
    return float(avail['Close'].iloc[-1]) if not avail.empty else None


def calc_swing_grade(stock_df: pd.DataFrame, swing_dates: list) -> dict:
    """
    swing_dates: ['2026-05-20', '2026-06-08', ...] 최소 2개
    Returns:
        grade   — S / A+ / A / A- / B / C / —
        pattern — '고→고→저' 형태 문자열
    """
    dates = sorted(set(str(d).strip() for d in swing_dates if str(d).strip()))
    if len(dates) < 2 or stock_df.empty:
        return {'grade': '—', 'pattern': ''}

    prices = [_close_on(stock_df, d) for d in dates]
    if any(p is None for p in prices):
        return {'grade': '—', 'pattern': '데이터부족'}

    labels = ['고' if prices[i] > prices[i - 1] else '저'
              for i in range(1, len(prices))]
    pattern = '→'.join(labels)

    n = len(labels)
    pos = labels.count('고')
    last_up = labels[-1] == '고'
    score = pos / n

    if score == 1.0:
        grade = 'S'
    elif score >= 0.67 and last_up:
        grade = 'A+'
    elif score >= 0.5 and last_up:
        grade = 'A'
    elif score >= 0.5 or (score > 0 and last_up):
        grade = 'A-'
    elif score > 0:
        grade = 'B'
    else:
        grade = 'C'

    return {'grade': grade, 'pattern': pattern}
