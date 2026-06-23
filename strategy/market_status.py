import pandas as pd
from strategy.indicators import calc_ema


def _ema21(index_df: pd.DataFrame) -> pd.Series:
    return calc_ema(index_df, 21)


def detect_jjin_bounce(index_df: pd.DataFrame) -> dict | None:
    """
    찐반등 감지 — 가장 최근 조건 충족일 반환.
    조건:
      1. 종가 < EMA21
      2. 당일 양봉, 상승폭 >= ADR(20일)
      3. 직전 봉 음봉 + 당일 바디 >= 직전 음봉 바디 * 0.5
    """
    if len(index_df) < 22:
        return None

    ema21    = _ema21(index_df)
    vol_ma20 = index_df['Volume'].rolling(20).mean()

    for i in range(len(index_df) - 1, 15, -1):
        row  = index_df.iloc[i]
        prev = index_df.iloc[i - 1]

        if float(row['Close']) >= float(ema21.iloc[i]):
            continue
        if float(row['Close']) < float(row['Open']):   # 양봉 아님
            continue
        if float(prev['Close']) >= float(prev['Open']): # 직전이 음봉 아님
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


def get_market_status(index_df: pd.DataFrame) -> dict:
    """
    지수 시장 상태 반환.
    state: 'normal' | 'correction' | 'early_signal'
    """
    base = {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None, 'jjin_pct': 0.0,
        'jjin_stars': 0,
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
    recs_after = rec_dates[rec_dates > last_start]

    base['correction_start'] = last_start

    if not is_below_now:
        base['state'] = 'normal'
        jjin = detect_jjin_bounce(index_df)
        if jjin and jjin['date'] >= last_start:
            base['jjin_date']  = jjin['date']
            base['jjin_pct']   = jjin['pct']
            base['jjin_stars'] = jjin['stars']
        return base

    jjin = detect_jjin_bounce(index_df)
    if jjin is None or jjin['date'] < last_start:
        base['state'] = 'correction'
        return base

    base.update({
        'state':       'early_signal',
        'jjin_date':   jjin['date'],
        'jjin_pct':    jjin['pct'],
        'jjin_stars':  jjin['stars'],
    })
    return base
