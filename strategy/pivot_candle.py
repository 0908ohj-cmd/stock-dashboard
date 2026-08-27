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
        if vr < 1.5:  # 쿨라매기 기준: 거래량 1.5배+ (이전 3배 기준에서 완화)
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
    """'없음' | '저가이탈' | '셋업(케이스1)' | '형성중(케이스1)' | '중간선이탈' | '10EMA이탈' | '돌파완료' | '형성중(케이스2)' | '셋업(케이스2)'

    셋업(케이스2): 기준봉 고가 부근 타이트 횡보 — 렐볼 브레이크아웃 대기
    셋업(케이스1): 기준봉 고가 돌파 후 10EMA ±3% 풀백 — 10EMA 지지 진입
    형성중(케이스2): 기준봉 있으나 셋업(케이스2) 조건 미충족 (베이스 무르익는 중)
    형성중(케이스1): 고가 돌파 이력 있고 10EMA 위 — 아직 10EMA까지 풀백 미진입 (대기)
    돌파완료: 타점을 이미 크게/오래 벗어남 → 추격 불가
    중간선이탈: 기준봉 (고+저)/2 아래 터치 → 셋업 무효
    10EMA이탈: 10EMA 아래 연속 2일 → 셋업 무효
    저가이탈: 기준봉 저가 하방 (기준봉이 이미 무효화된 경우 포함)
    없음: 최근 lookback(기본 63) 거래일 내 기준봉 후보 자체가 없음
    """
    if pivot is None:
        return '없음'

    current_close = float(stock_df['Close'].iloc[-1])

    if pivot.get('invalidated') or current_close < pivot['low']:
        return '저가이탈'

    since_pivot = stock_df[stock_df.index > pivot['date']]

    # 케이스1 계열: 기준봉 고가 돌파 이력 있고 10EMA>pivot*1.02 + days_above<=20
    # - 셋업(케이스1): 현재가 10EMA ±3% 이내 → 진입 타이밍
    # - 형성중(케이스1): 현재가 아직 10EMA 위 → 풀백 대기
    if not since_pivot.empty and bool((since_pivot['Close'] > pivot['high']).any()):
        _ema10 = float(calc_ema(stock_df, 10).iloc[-1])
        _ema21 = float(calc_ema(stock_df, 21).iloc[-1])
        _after_cluster = stock_df[stock_df.index > pivot.get('cluster_end', pivot['date'])]
        _days_above = int((_after_cluster['Close'] > pivot['high']).sum())
        if _ema10 > _ema21 and _ema10 > pivot['high'] * 1.02 and _days_above <= 20:
            if _ema10 * 0.97 <= current_close <= _ema10 * 1.03:
                return '셋업(케이스1)'
            if current_close > _ema10 * 1.03:
                return '형성중(케이스1)'

    # 이미 타점을 크게 돌파 → 추격 불가 (ADR 1.5배 초과 or 기준봉 고가 위 누적 5거래일 초과).
    # 누적일은 클러스터가 끝난 뒤부터 센다 — 같은 흐름 안의 후행봉은 기준봉 고가 위에서
    # 마감하는 게 정상이라, 첫 봉부터 세면 갓 완성된 셋업이 돌파완료로 뒤집힌다
    after_cluster = stock_df[stock_df.index > pivot.get('cluster_end', pivot['date'])]
    if not since_pivot.empty:
        adr = float(((stock_df['High'] - stock_df['Low']) / stock_df['Close'] * 100).rolling(20).mean().iloc[-1])
        days_above = int((after_cluster['Close'] > pivot['high']).sum())
        if current_close > pivot['high'] * (1 + adr * 1.5 / 100) or days_above > 5:
            return '돌파완료'

    # 연속 2거래일 중간선 아래 → 이탈 (1회는 허용)
    if len(since_pivot) >= 2:
        below_mid = since_pivot['Close'] < pivot['midline']
        if (below_mid & below_mid.shift(1)).any():
            return '중간선이탈'

    if len(since_pivot) >= 2:
        ema10_since = calc_ema(stock_df, 10).loc[since_pivot.index]
        below = since_pivot['Close'] < ema10_since
        if (below & below.shift(1)).any():
            return '10EMA이탈'

    # 기준봉 이후 실제 거래일 수 — busday는 휴장일을 거래일로 세버림
    days_since = len(since_pivot)

    # 셋업 A: 잠깐 돌파 후 기준봉 고가 부근 복귀 (재진입 기회)
    if days_since <= 30 and not since_pivot.empty:
        max_high         = float(since_pivot['High'].max())
        ever_above       = max_high > pivot['high']
        not_overextended = max_high <= pivot['high'] * 1.10
        days_above_high  = int((after_cluster['Close'] > pivot['high']).sum())
        brief_stay       = days_above_high <= 5
        back_near        = pivot['high'] * 0.97 <= current_close <= pivot['high'] * 1.05
        if ever_above and not_overextended and brief_stay and back_near:
            return '셋업(케이스2)'

    # 셋업(케이스2) B: 기준봉 고가 아래 타이트 횡보 — 거래량 수축 + EMA 서핑
    in_range   = pivot['midline'] <= current_close <= pivot['high'] * 1.03
    valid_days = 3 <= days_since <= 40
    slope_up   = calc_10ema_slope(stock_df) > 0
    ema10_now  = float(calc_ema(stock_df, 10).iloc[-1])
    above_ema  = current_close > ema10_now
    base_tight = _base_is_tight(since_pivot, pivot['high'])

    if in_range and valid_days and slope_up and above_ema and base_tight:
        pivot_pos   = stock_df.index.get_loc(pivot['date'])
        pre_vol_avg = float(stock_df['Volume'].iloc[max(0, pivot_pos - 20):pivot_pos].mean())
        if since_pivot.empty or pre_vol_avg == 0:
            vol_dry_up = True
        else:
            consol_avg = float(since_pivot['Volume'].mean())
            recent_avg = float(since_pivot['Volume'].tail(min(3, len(since_pivot))).mean())
            vol_dry_up = consol_avg <= pre_vol_avg * 0.8 and recent_avg <= consol_avg
        if vol_dry_up:
            return '셋업(케이스2)'

    return '형성중(케이스2)'
