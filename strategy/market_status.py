import pandas as pd
from strategy.indicators import calc_ema


def _ema21(index_df: pd.DataFrame) -> pd.Series:
    return calc_ema(index_df, 21)


def detect_jjin_bounce(index_df: pd.DataFrame) -> dict | None:
    """
    찐반등 감지 — 가장 최근 조건 충족일 반환.
    조건:
      1. 장중 저가가 EMA21 아래
      2. 당일 양봉, 상승폭 >= ADR(20일)
      3. 직전 봉 음봉 + 당일 바디 >= 직전 음봉 바디 * 0.5
    거래량 별: 조정 구간(EMA21 이탈일 ~ 전날) 평균 대비 비율
    """
    if len(index_df) < 22:
        return None

    ema21 = _ema21(index_df)
    below = index_df['Close'] < ema21
    bd_starts = below.astype(int).diff()[lambda s: s == 1].index  # EMA21 이탈일 목록
    vol_ma20  = index_df['Volume'].rolling(20).mean()              # fallback용

    for i in range(len(index_df) - 1, 15, -1):
        row  = index_df.iloc[i]
        prev = index_df.iloc[i - 1]

        # OHLC 불완전 행(Close만 채워진 패치 잔재 등)은 NaN 비교가 모든 가드를
        # False로 통과시켜 오검출되므로 명시적으로 제외
        if row[['Open', 'High', 'Low', 'Close']].isna().any():
            continue
        if float(row['Low']) >= float(ema21.iloc[i]):
            continue
        if float(row['Close']) < float(row['Open']):
            continue
        if i < 2:
            continue
        prev2 = index_df.iloc[i - 2]

        adr_val = float(
            ((index_df['High'] - index_df['Low']) / index_df['Close'] * 100)
            .iloc[max(0, i - 19):i + 1].mean()
        )

        # 조건 3: 직전봉이 음봉이거나, 양봉이더라도 ADR/2 미만 상승 (지지부진)
        prev_chg_pct = (float(prev['Close']) - float(prev2['Close'])) / float(prev2['Close']) * 100
        if prev_chg_pct >= adr_val / 2:
            continue

        prev_close = float(index_df.iloc[i - 1]['Close'])
        pct_chg    = (float(row['Close']) - prev_close) / prev_close * 100
        if pct_chg < adr_val:
            continue

        curr_body = abs(float(row['Close']) - float(row['Open']))
        prev_body = abs(float(prev['Close']) - float(prev['Open']))
        if prev_body == 0 or curr_body < prev_body * 0.5:
            continue

        # 조정 구간 평균 거래량으로 vol_ratio 계산
        bounce_date   = index_df.index[i]
        bounce_vol    = float(row['Volume'])
        starts_before = bd_starts[bd_starts <= bounce_date]

        vol_ratio = 0.0
        if len(starts_before) > 0:
            corr_start  = starts_before[-1]
            corr_slice  = index_df[(index_df.index >= corr_start) & (index_df.index < bounce_date)]
            valid_vol   = corr_slice.loc[corr_slice['Volume'] > 0, 'Volume']
            if len(valid_vol) >= 20 and bounce_vol > 0:
                vol_ratio = bounce_vol / float(valid_vol.mean())

        if vol_ratio == 0.0:  # 조정 구간 짧거나 데이터 없으면 20일 이동평균으로 fallback
            vol_ma    = float(vol_ma20.iloc[i]) if not pd.isna(vol_ma20.iloc[i]) else 0
            vol_ratio = bounce_vol / vol_ma if vol_ma > 0 else 0.0

        stars = 3 if vol_ratio >= 1.2 else (2 if vol_ratio >= 1.0 else 1)

        return {
            'date':      bounce_date,
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
    """찐반등 저점 아래 종가 등장 시 True → 즉시 DAY1 복귀."""
    if jjin_date not in index_df.index:
        return False
    jjin_low = float(index_df.loc[jjin_date, 'Low'])
    after = index_df[index_df.index > jjin_date]
    for _, row in after.iterrows():
        if float(row['Close']) < jjin_low:
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

    # 찐반등 저점 아래 종가 등장 → 기간 무관 즉시 DAY1 복귀
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
