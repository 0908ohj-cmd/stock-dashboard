import pandas as pd
from strategy.indicators import calc_ema


def calc_10ema_slope(stock_df: pd.DataFrame, period: int = 5) -> float:
    if len(stock_df) < period + 10:
        return 0.0
    ema10 = calc_ema(stock_df, 10)
    base = float(ema10.iloc[-(period + 1)])
    if base == 0:
        return 0.0
    return float((ema10.iloc[-1] - base) / base * 100)


def _has_prior_move(df: pd.DataFrame, pivot_idx: int, pivot_high: float,
                    lookback: int = 65, min_pct: float = 30.0) -> bool:
    """기준봉 이전 lookback 거래일 내 저점 → 기준봉 고가까지 min_pct% 이상 상승 여부."""
    start = max(0, pivot_idx - lookback)
    if pivot_idx <= start:
        return False
    prior_low = float(df['Low'].iloc[start:pivot_idx].min())
    if prior_low <= 0:
        return False
    return (pivot_high / prior_low - 1) * 100 >= min_pct


def _base_is_tight(since_pivot: pd.DataFrame, pivot_high: float,
                   max_range_pct: float = 15.0) -> bool:
    """베이스 타이트함: 횡보 구간 High-Low 범위가 기준봉 고가 대비 max_range_pct 이내."""
    if since_pivot.empty:
        return True
    base_high = float(since_pivot['High'].max())
    base_low  = float(since_pivot['Low'].min())
    if pivot_high <= 0:
        return True
    return (base_high - base_low) / pivot_high * 100 <= max_range_pct


def _vol_ratio_at(df: pd.DataFrame, idx: int, window: int = 20) -> float:
    if idx < window:
        return 0.0
    avg = float(df['Volume'].iloc[idx - window:idx].mean())
    if avg == 0:
        return 0.0
    return float(df['Volume'].iloc[idx]) / avg


def _close_in_top30(row: pd.Series) -> bool:
    rng = float(row['High']) - float(row['Low'])
    if rng == 0:
        return False
    return (float(row['Close']) - float(row['Low'])) / rng >= 0.7


def _is_aligned(ema10: pd.Series, ema21: pd.Series, ema50: pd.Series, idx: int) -> bool:
    return float(ema10.iloc[idx]) > float(ema21.iloc[idx]) > float(ema50.iloc[idx])


def _broke_60d_high(df: pd.DataFrame, idx: int) -> bool:
    if idx < 60:
        return False
    prior_high = float(df['High'].iloc[idx - 60:idx].max())
    return float(df['Close'].iloc[idx]) > prior_high


def _broke_vcp_box(df: pd.DataFrame, idx: int,
                   min_days: int = 5, max_days: int = 20,
                   max_range_pct: float = 5.0) -> bool:
    """직전 5~20일 범위가 5% 이내(횡보 박스)이면 박스 상단 돌파 여부 반환."""
    for lookback in range(min_days, min(max_days + 1, idx)):
        window = df.iloc[idx - lookback:idx]
        hi = float(window['High'].max())
        lo = float(window['Low'].min())
        if lo == 0:
            continue
        if (hi - lo) / lo * 100 <= max_range_pct:
            # 박스 저항선은 고가(hi)다. 종가최대를 쓰면 윗꼬리로 만들어진 실제 고점
            # 아래에서 마감해도 돌파로 잡혀 유령 기준봉·유령 타점이 생긴다
            if float(df['Close'].iloc[idx]) > hi:
                return True
    return False


_CLUSTER_GAP = 10   # 직전 후보와 이 거래일 이내면 같은 상승 흐름으로 본다


def _cluster_candidates(candidates: list, max_gap: int = _CLUSTER_GAP) -> list:
    """직전 후보와 max_gap 거래일 이내로 이어지는 후보들을 한 클러스터로 묶는다.

    간격을 클러스터 첫 봉이 아니라 '직전 후보'에서 재야 78/85/92처럼 7거래일씩
    연쇄하는 한 흐름이 둘로 갈리지 않는다.
    """
    clusters = [[candidates[0]]]
    for cand in candidates[1:]:
        if cand[0] - clusters[-1][-1][0] > max_gap:
            clusters.append([cand])
        else:
            clusters[-1].append(cand)
    return clusters


def _low_broken_after(df: pd.DataFrame, idx: int) -> bool:
    """기준봉 저가가 이후 종가로 뚫렸는지 — 손절선이 이미 깨진 자리다."""
    pivot_low = float(df['Low'].iloc[idx])
    return bool((df['Close'].iloc[idx + 1:] < pivot_low).any())


def _pivot_result(df: pd.DataFrame, cand: tuple, cluster_end_idx: int,
                  invalidated: bool = False) -> dict:
    i, vr = cand
    row   = df.iloc[i]
    high, low, close = float(row['High']), float(row['Low']), float(row['Close'])
    return {
        'date':      df.index[i],
        'vol_ratio': round(vr, 2),
        'high':      high,
        'low':       low,
        'midline':   round((high + low) / 2, 4),
        'close':     close,
        # 같은 흐름의 마지막 후보 — 과열(돌파완료) 판정을 여기서부터 센다
        'cluster_end': df.index[cluster_end_idx],
        # 저가가 이미 뚫린 기준봉. None으로 지우면 '없음'으로 표시돼 저가이탈이 가려진다
        'invalidated': invalidated,
    }


def find_pivot_candle(
    stock_df: pd.DataFrame,
    lookback: int = 63,
) -> dict | None:
    """
    최근 lookback 거래일 내 기준봉 탐지.
    조건: 거래량 150%+(1.5배), 종가 레인지 상위 30%, 저항 돌파(60일 고점 or VCP 박스), 정배열.
    복수 후보 시 (거래량비율로 고르지 않는다 — 타점이 기준봉 고가라 같은 흐름
    안에서는 첫 봉이 낮은 진입가를 준다):
      1) 전체 후보를 클러스터로 묶는다. 무효화를 먼저 걸러내면 버려진 후보 유무가
         살아남은 후보 간 선택을 뒤집으므로 순서가 중요하다
      2) 최신 클러스터부터 거슬러 올라가며, 저가가 살아있는 첫 봉을 쓴다
      3) 살아있는 후보가 하나도 없으면 가장 최근 후보를 invalidated로 돌려준다
         (None을 주면 저가를 깬 종목이 '기준봉 미탐지'로 보인다)
    후보 자체가 없을 때만 None.
    """
    if len(stock_df) < 70:
        return None

    ema10  = calc_ema(stock_df, 10)
    ema21  = calc_ema(stock_df, 21)
    ema50  = stock_df['Close'].rolling(50).mean()
    sma150 = stock_df['Close'].rolling(150).mean()
    sma200 = stock_df['Close'].rolling(200).mean()

    start_idx = max(60, len(stock_df) - lookback)
    candidates = []

    for i in range(start_idx, len(stock_df) - 1):  # 오늘(마지막 봉) 제외
        vr = _vol_ratio_at(stock_df, i)
        if vr < 2.0:  # 거래량 2배+ 기준
            continue
        row = stock_df.iloc[i]
        if not _close_in_top30(row):
            continue
        if not (_broke_60d_high(stock_df, i) or _broke_vcp_box(stock_df, i)):
            continue
        if pd.isna(ema50.iloc[i]):
            continue
        if not _is_aligned(ema10, ema21, ema50, i):
            continue
        # 장기 이평선 위 — 장기 하락 중 단기 반등 종목 제외
        close_i = float(row['Close'])
        if not pd.isna(sma150.iloc[i]) and close_i < float(sma150.iloc[i]):
            continue
        if not pd.isna(sma200.iloc[i]) and close_i < float(sma200.iloc[i]):
            continue
        # 쿨라매기 조건: 기준봉 이전 65거래일(3개월) 내 30%+ 상승 구간 존재
        if not _has_prior_move(stock_df, i, float(row['High'])):
            continue
        candidates.append((i, vr))

    if not candidates:
        return None

    # 클러스터를 '전체 후보'로 먼저 묶는다 — 무효화를 앞세우면 결과에 없는 후보가
    # 경계를 옮겨 살아남은 후보 간 선택을 뒤집는다
    clusters = _cluster_candidates(candidates)

    for cluster in reversed(clusters):          # 최신 클러스터부터
        alive = [c for c in cluster if not _low_broken_after(stock_df, c[0])]
        if alive:
            return _pivot_result(stock_df, alive[0], cluster[-1][0])

    # 살아남은 후보 없음 → 가장 최근 후보를 무효 표시로 반환 (저가이탈 표시용)
    last = candidates[-1]
    return _pivot_result(stock_df, last, last[0], invalidated=True)


def classify_case(
    stock_df: pd.DataFrame,
    pivot: dict | None,
) -> str:
    """'없음' | '저가이탈' | '셋업(케이스1)' | '셋업(케이스2)' | '중간선이탈' | '10EMA이탈' | '돌파완료'

    셋업(케이스1): 기준봉 고가 돌파 이력 있음 + 이탈 없음 + 10EMA 위 연속 10일 미만 → 10EMA 풀백 진입 대기
    셋업(케이스2): 고가 미돌파 + 이탈 없음 → 브레이크아웃 대기
    돌파완료: 케이스1 territory에서 10EMA 위 종가 10거래일 이상 연속 → 진입 기회 지남
    중간선이탈: 기준봉 중간선 아래 연속 2거래일 → 셋업 무효
    10EMA이탈: 10EMA 아래 연속 2거래일 → 셋업 무효
    저가이탈: 기준봉 저가 하방 (무효화 포함)
    없음: lookback(기본 63) 거래일 내 기준봉 후보 없음
    """
    if pivot is None:
        return '없음'

    current_close = float(stock_df['Close'].iloc[-1])

    if pivot.get('invalidated') or current_close < pivot['low']:
        return '저가이탈'

    since_pivot = stock_df[stock_df.index > pivot['date']]

    # 이탈 계열 — 케이스1/2 공통, 먼저 체크
    if len(since_pivot) >= 2:
        below_mid = since_pivot['Close'] < pivot['midline']
        if (below_mid & below_mid.shift(1)).any():
            return '중간선이탈'

    ema10_since = calc_ema(stock_df, 10).loc[since_pivot.index] if not since_pivot.empty else None

    if len(since_pivot) >= 2 and ema10_since is not None:
        below_ema = since_pivot['Close'] < ema10_since
        if (below_ema & below_ema.shift(1)).any():
            return '10EMA이탈'

    # 케이스1 territory: ever_above=True (기준봉 고가 돌파 이력)
    ever_above = not since_pivot.empty and bool((since_pivot['Close'] > pivot['high']).any())

    if ever_above:
        # 돌파완료: 기준봉 이후 10EMA 위 종가 누적일(연속 아님) >= 10 → 진입 기회 이미 지남
        if ema10_since is not None:
            above_ema = since_pivot['Close'] > ema10_since
            if int(above_ema.sum()) >= 10:
                return '돌파완료'
        return '셋업(케이스1)'

    # 케이스2: 고가 미돌파
    return '셋업(케이스2)'
