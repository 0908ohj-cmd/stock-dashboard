import pandas as pd
from strategy.indicators import calc_ema


def _ema21(index_df: pd.DataFrame) -> pd.Series:
    return calc_ema(index_df, 21)


def detect_jjin_bounce(index_df: pd.DataFrame) -> dict | None:
    """
    찐반등 감지 — 가장 최근 조건 충족일 반환.
    조건:
      1. 장중 EMA21 아래 터치 (Low < EMA21)
      2. 당일 양봉, 전일 종가 대비 상승폭 >= ADR(20일)
      3. 직전 봉이 하락 중(전전일 종가 대비 하락) + 당일 바디 >= 직전 봉 바디 * 0.5
    """
    if len(index_df) < 22:
        return None

    ema21    = _ema21(index_df)
    vol_ma20 = index_df['Volume'].rolling(20).mean()

    for i in range(len(index_df) - 1, 15, -1):
        row  = index_df.iloc[i]
        prev = index_df.iloc[i - 1]

        if float(row['Low']) >= float(ema21.iloc[i]):
            continue  # 장중에도 EMA21 아래 안 내려왔으면 스킵
        if float(row['Close']) < float(row['Open']):   # 양봉 아님
            continue
        if i < 2:
            continue
        prev2 = index_df.iloc[i - 2]
        if float(prev['Close']) >= float(prev2['Close']): # 직전 종가가 그 전날보다 높음 = 상승 중
            continue

        prev_close = float(index_df.iloc[i - 1]['Close'])
        pct_chg    = (float(row['Close']) - prev_close) / prev_close * 100
        adr_val    = float(
            ((index_df['High'] - index_df['Low']) / index_df['Close'] * 100)
            .iloc[max(0, i - 19):i + 1].mean()
        )
        if pct_chg < adr_val:
            continue

        curr_body = abs(float(row['Close']) - float(row['Open']))
        prev_body = abs(float(prev['Close']) - float(prev['Open']))
        if prev_body == 0 or curr_body < prev_body * 0.5:  # 직전 음봉 바디 50% 이상 커버
            continue

        vol_ma    = float(vol_ma20.iloc[i]) if not pd.isna(vol_ma20.iloc[i]) else 0
        vol_ratio = float(row['Volume']) / vol_ma if vol_ma > 0 else 0
        stars     = 3 if vol_ratio >= 1.2 else (2 if vol_ratio >= 1.0 else 1)

        return {
            'date':      index_df.index[i],
            'pct':       round(pct_chg, 2),
            'adr':       round(adr_val, 2),
            'cover_pct': round(curr_body / prev_body * 100, 1),
            'vol_ratio': round(vol_ratio, 2),
            'stars':     stars,
        }

    return None


def _detect_ftd(index_df: pd.DataFrame, jjin_date: pd.Timestamp) -> pd.Timestamp | None:
    """FTD 확인 (보조): jjin_date 이후 Day4+, +1.7%, 전일 거래량 초과"""
    after = index_df[index_df.index > jjin_date]
    if len(after) < 4:
        return None

    jjin_low   = float(index_df.loc[jjin_date, 'Low'])
    after_list = list(after.iterrows())

    for i, (idx, row) in enumerate(after_list):
        if float(row['Low']) < jjin_low:
            return None
        if i < 3:
            continue
        prev_close = float(after_list[i - 1][1]['Close'])
        prev_vol   = float(after_list[i - 1][1]['Volume'])
        pct = (float(row['Close']) - prev_close) / prev_close * 100
        if pct >= 1.7 and float(row['Volume']) > prev_vol:
            return idx

    return None


def _jjin_failed(index_df: pd.DataFrame, jjin_date: pd.Timestamp,
                  ema21: pd.Series, window: int = 3) -> bool:
    """jjin_date 이후 window 거래일이 모두 지난 후에도 EMA21 위로 종가 못 닫혔으면 True.
    window 일 당일(DAY5)에는 아직 실패 판정 안 함 → day4(window+1)부터 판정."""
    after = index_df[index_df.index > jjin_date]
    if len(after) <= window:
        return False  # window일 이하 → 대기 중 (DAY3~5 진행 중)
    for idx, row in after.head(window).iterrows():
        if idx in ema21.index and float(row['Close']) > float(ema21[idx]):
            return False  # window 내 EMA21 위로 닫힘 → 성공
    return True  # 실패


def _jjin_engulfed(index_df: pd.DataFrame, jjin_date: pd.Timestamp) -> bool:
    """찐반등 이후 그 봉 바디를 잡아먹는 음봉(바디 >= 찐반등 바디) 출현 시 True → 즉시 DAY1 복귀."""
    if jjin_date not in index_df.index:
        return False
    jjin_row  = index_df.loc[jjin_date]
    jjin_body = abs(float(jjin_row['Close']) - float(jjin_row['Open']))
    if jjin_body == 0:
        return False
    after = index_df[index_df.index > jjin_date]
    for _, row in after.iterrows():
        if float(row['Close']) >= float(row['Open']):  # 양봉 스킵
            continue
        bear_body = float(row['Open']) - float(row['Close'])
        if bear_body >= jjin_body:  # 찐반등 바디 전부 잡아먹는 음봉
            return True
    return False


def get_market_status(index_df: pd.DataFrame) -> dict:
    """
    지수 시장 상태 반환.
    state: 'normal' | 'correction' | 'early_signal'
    찐반등 감지 후 3거래일 내 EMA21 미회복 시 실패로 판정, 조정 상태로 복귀.
    """
    base = {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None, 'jjin_pct': 0.0,
        'jjin_stars': 0, 'failed_jjin_date': None,
    }
    if len(index_df) < 22:
        return base

    ema21        = _ema21(index_df)
    below        = index_df['Close'] < ema21
    transitions  = below.astype(int).diff()
    bd_starts    = transitions[transitions == 1].index
    rec_dates    = transitions[transitions == -1].index
    is_below_now = bool(below.iloc[-1])

    if len(bd_starts) == 0:
        return base

    last_start = bd_starts[-1]

    base['correction_start'] = last_start

    if not is_below_now:
        base['state'] = 'normal'

        # EMA21 위 연속 거래일 카운트
        consecutive_above = 0
        for i in range(len(index_df) - 1, -1, -1):
            if float(index_df['Close'].iloc[i]) < float(ema21.iloc[i]):
                break
            consecutive_above += 1

        # 찐반등 탐색 (EMA21 회복 기간 무관)
        jjin = detect_jjin_bounce(index_df)
        if jjin and jjin['date'] >= last_start:
            base['jjin_date']  = jjin['date']
            base['jjin_pct']   = jjin['pct']
            base['jjin_stars'] = jjin['stars']
        # correction_start·jjin_date 유지 → 다음 DAY1(새 이탈) 전까지 핵심 후보 계속 노출
        return base

    # 지수가 EMA21 아래인 상태
    jjin = detect_jjin_bounce(index_df)
    if jjin is None or jjin['date'] < last_start:
        base['state'] = 'correction'
        return base

    # 찐반등 후 3거래일 내 EMA21 회복 실패 여부 확인
    if _jjin_failed(index_df, jjin['date'], ema21, window=3):
        base['state']            = 'correction'
        base['failed_jjin_date'] = jjin['date']
        return base

    # 찐반등 봉을 잡아먹는 음봉 출현 → 기간 무관 즉시 DAY1 복귀
    if _jjin_engulfed(index_df, jjin['date']):
        base['state']            = 'correction'
        base['failed_jjin_date'] = jjin['date']
        return base

    # 찐반등 감지, 아직 확인 대기 중 (3거래일 이내)
    base.update({
        'state':       'early_signal',
        'jjin_date':   jjin['date'],
        'jjin_pct':    jjin['pct'],
        'jjin_stars':  jjin['stars'],
    })
    return base
