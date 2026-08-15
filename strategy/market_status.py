import pandas as pd
from strategy.indicators import calc_ema


def _ema21(index_df: pd.DataFrame) -> pd.Series:
    return calc_ema(index_df, 21)


def detect_jjin_bounce(index_df: pd.DataFrame,
                       start: pd.Timestamp | None = None) -> dict | None:
    """
    찐반등 감지 — 가장 최근 조정 시작일 이후 첫 번째 조건 충족일 반환.
    조건:
      1. 장중 저가가 EMA21 아래
      2. 당일 양봉, 상승폭 >= ADR(20일)
      3. 직전 봉 음봉 + 당일 바디 >= 직전 음봉 바디 * 0.5
    거래량 별: 조정 구간(EMA21 이탈일 ~ 전날) 평균 대비 비율
    start: 이 날짜(포함)부터만 탐색 — 실패한 찐반등 이후 새 후보 탐색용.
    """
    if len(index_df) < 22:
        return None

    ema21 = _ema21(index_df)
    below = index_df['Close'] < ema21
    bd_starts = below.astype(int).diff()[lambda s: s == 1].index  # EMA21 이탈일 목록
    vol_ma20  = index_df['Volume'].rolling(20).mean()              # fallback용

    if len(bd_starts) == 0:
        return None

    last_start = bd_starts[-1]
    try:
        last_start_loc = index_df.index.get_loc(last_start)
    except KeyError:
        return None

    # 조정 구간 최저 Low 이후부터 탐색 — EMA21이 장기 이탈 중이어도
    # 가장 최근 바닥 이후 첫 번째 반등을 찐반등으로 인식하기 위함
    correction_slice = index_df.iloc[last_start_loc:]
    lowest_low_loc   = last_start_loc + int(correction_slice['Low'].argmin())
    start_loc        = max(lowest_low_loc, 2)
    if start is not None:
        start_loc = max(start_loc, int(index_df.index.searchsorted(start)))

    for i in range(start_loc, len(index_df)):
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


def _jjin_failure_date(index_df: pd.DataFrame, jjin_date: pd.Timestamp,
                       ema21: pd.Series, window: int = 5) -> pd.Timestamp | None:
    """찐반등 실패가 확정된 날짜 반환. 실패 아니거나 아직 대기 중이면 None.
    - 종가 < 찐반등 저가: 그날 즉시 실패 (기간 무관)
    - window 거래일 내 EMA21 위 종가 없음: window+1번째 거래일에 확정
      (window일 당일(DAY7)에는 아직 실패 판정 안 함)"""
    if jjin_date not in index_df.index:
        return None

    def _scalar(v):
        """중복 날짜가 섞이면 .loc/[]가 Series를 반환한다 — 마지막 값으로 축약."""
        return float(v.iloc[-1] if isinstance(v, pd.Series) else v)

    jjin_low = _scalar(index_df.loc[jjin_date, 'Low'])
    after = index_df[index_df.index > jjin_date]
    recovered = False
    for i, (idx, row) in enumerate(after.iterrows()):
        close = float(row['Close'])
        if close < jjin_low:
            return idx
        if i < window and idx in ema21.index and close > _scalar(ema21[idx]):
            recovered = True
        if i == window and not recovered:
            return idx
    return None


def _resolve_jjin(index_df: pd.DataFrame, ema21: pd.Series,
                  last_start: pd.Timestamp) -> tuple[dict | None, pd.Timestamp | None]:
    """실패한 찐반등을 건너뛰며 현재 유효한 후보를 찾는다 → (찐반등 | None, 마지막 실패일 | None).

    실패 확정일이 아니라 실패 후보의 '다음 거래일'부터 재탐색한다 — 실패 후보의 확인
    대기창(DAY3~7) 안에서 나온 새 찐반등도 놓치지 않기 위함.
    """
    search_start = None
    last_failed  = None
    for _ in range(len(index_df)):        # 후보 날짜가 매 회 엄격히 증가하므로 실제로는 조기 종료
        jjin = detect_jjin_bounce(index_df, start=search_start)
        if jjin is None or jjin['date'] < last_start:
            return None, last_failed
        if _jjin_failure_date(index_df, jjin['date'], ema21, window=5) is None:
            return jjin, last_failed
        last_failed = jjin['date']
        nxt = int(index_df.index.searchsorted(jjin['date'], side='right'))
        if nxt >= len(index_df):
            return None, last_failed
        search_start = index_df.index[nxt]
    return None, last_failed


def get_market_status(index_df: pd.DataFrame) -> dict:
    """
    지수 시장 상태 반환.
    state: 'normal' | 'correction' | 'early_signal'
    찐반등 감지 후 5거래일 내 EMA21 미회복(또는 저점 이탈) 시 실패 판정 → 조정 복귀.
    실패 확정일 이후의 새 찐반등 후보는 다시 탐색한다.
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
    is_below_now = bool(below.iloc[-1])

    if len(bd_starts) == 0:
        return base

    last_start = bd_starts[-1]

    base['correction_start'] = last_start

    # 실패한 찐반등은 건너뛰고 유효 후보를 찾는다 — EMA21 회복 여부와 무관하게 적용해야
    # '실패한 반등이 이후의 진짜 반등을 가리는' 문제가 두 분기 모두에서 사라진다
    jjin, last_failed = _resolve_jjin(index_df, ema21, last_start)

    if not is_below_now:
        # correction_start·jjin_date 유지 → 다음 DAY1(새 이탈) 전까지 핵심 후보 계속 노출
        base['state'] = 'normal'
        # 실패 이력도 함께 전달 — 없으면 UI가 '찐반등 미확인'으로 표시해
        # 반등 시도 자체가 없었던 것처럼 읽힌다
        base['failed_jjin_date'] = last_failed
        if jjin:
            base['jjin_date']  = jjin['date']
            base['jjin_pct']   = jjin['pct']
            base['jjin_stars'] = jjin['stars']
        return base

    if jjin is None:
        base['state']            = 'correction'
        base['failed_jjin_date'] = last_failed
        return base

    # 찐반등 감지, 아직 확인 대기 중
    base.update({
        'state':       'early_signal',
        'jjin_date':   jjin['date'],
        'jjin_pct':    jjin['pct'],
        'jjin_stars':  jjin['stars'],
    })
    return base
