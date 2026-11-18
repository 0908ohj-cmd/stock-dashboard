from datetime import datetime

import streamlit as st
import pandas as pd
import streamlit.components.v1 as components
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

from strategy.trading_days import trading_days_after
from data.fetcher import (
    get_stock_name,
    fetch_intraday_for_date, fetch_index_intraday_for_date,
)
from data import store
from data.sector import get_sectors, get_display_sectors
from data import leaderboard_store
from strategy.market_status import get_market_status
from strategy.rs_correction import calc_correction_rs, _index_peak_date, clean_manual_rs_range, resolve_rs_range
from strategy.indicators import calc_pct_from_52w_high
from ui.intraday_overlay import intraday_overlay_chart
from ui import rs_range, tv_export
from ui.sector_leadership import lead_scores
from ui import sector_leadership
from data import classification
from ui.watchlist_logic import (
    chart_position, chart_step, nav_button_keys, space_nav_script,
    lead_gap_reasons,
    INTRADAY_CACHE_TTL, intraday_empty_reason,
)

# 매수 후보 카드 — 2026-11-10부터 끔(주도섹터 탭 차기 주도주로 일원화). 켜면 주도주 점수 상위 N개를 보여준다.
SHOW_CANDIDATES = False
CANDIDATE_TOP_N = 9

INDEX_FOR_MARKET = {
    'KR_KOSPI':  'KOSPI',
    'KR_KOSDAQ': 'KOSDAQ',
    'US':        'NASDAQ',
}

# UI 시장 코드 → 리더보드 시장 코드
_LB_MARKET = {'KR_KOSPI': 'kr', 'KR_KOSDAQ': 'kr', 'US': 'us'}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


@st.cache_data(ttl=120)
def _snapshot_ts(market: str) -> str:
    """스냅샷 fetched_at 타임스탬프 — 변경 시 _build_rows·_fetch_index·10EMA 표 캐시 자동 무효화.
    TTL(120초) 동안은 옛 값을 주므로, 스냅샷을 다시 쓴 쪽은 이 캐시를 직접 비운다."""
    return store.load_snapshot(market).get('fetched_at', '')


@st.cache_data(ttl=1800)
def _fetch_index_cached(name: str, ts: str = '') -> pd.DataFrame:
    return store.load_index(name)


@st.cache_data(ttl=1800)
def _get_market_status_cached(market: str, ts: str = '') -> dict:
    index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')
    index_df   = _fetch_index_cached(index_name, ts)
    return get_market_status(index_df) if not index_df.empty else {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None,  'jjin_pct': 0.0,
        'jjin_stars': 0,    'ftd_date': None,
    }


@st.cache_data(ttl=INTRADAY_CACHE_TTL, show_spinner=False)
def _stock_intraday_cached(ticker: str, jjin_date_str: str, market: str) -> pd.DataFrame:
    """찐반등일 포함 5거래일 종목 5분봉 — (티커, 찐반등일) 키로 캐시 (#39 M14).
    캐시가 없으면 rerun마다(어느 위젯을 만지든) 다시 받아 야후 세션 예산을 잠식했다.
    예외는 빈 결과로 바꿔 같은 TTL 동안 다시 묻지 않는다 — 예외는 캐시되지 않아 rerun마다 재시도된다."""
    try:
        return fetch_intraday_for_date(ticker, pd.Timestamp(jjin_date_str), market=market, days=5)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=INTRADAY_CACHE_TTL, show_spinner=False)
def _index_intraday_cached(index_name: str, jjin_date_str: str) -> pd.DataFrame:
    """찐반등일 포함 5거래일 지수 5분봉 — (지수, 찐반등일) 키로 캐시 (#39 M14)."""
    try:
        return fetch_index_intraday_for_date(index_name, pd.Timestamp(jjin_date_str), days=5)
    except Exception:
        return pd.DataFrame()


def _ma_position(stock_df: pd.DataFrame, index_df: pd.DataFrame) -> tuple[str, int]:
    """지수가 이탈한 이평선 기준, 종목 위/아래 텍스트 반환. (text, above_count)"""
    if index_df.empty or len(index_df) < 50:
        return '', 0

    idx_close = float(index_df['Close'].iloc[-1])
    stk_close = float(stock_df['Close'].iloc[-1])

    def _ema(df, n): return float(df['Close'].ewm(span=n, adjust=False).mean().iloc[-1])
    def _sma(df, n): return float(df['Close'].rolling(n).mean().iloc[-1]) if len(df) >= n else float('nan')

    levels = [
        ('EMA21',  _ema(index_df, 21),  _ema(stock_df, 21)),
        ('SMA50',  _sma(index_df, 50),  _sma(stock_df, 50)),
        ('SMA150', _sma(index_df, 150), _sma(stock_df, 150)),
        ('SMA200', _sma(index_df, 200), _sma(stock_df, 200)),
    ]

    parts = []
    above_count = 0
    for name, idx_ma, stk_ma in levels:
        if pd.isna(idx_ma) or pd.isna(stk_ma):
            continue
        if idx_close < idx_ma:  # 지수가 이 이평선 아래 (이탈)
            if stk_close > stk_ma:
                parts.append(f'{name}위')
                above_count += 1
            else:
                parts.append(f'{name}아래')

    return (' · '.join(parts) if parts else '지수정상'), above_count


@st.cache_data(ttl=1800)
def _build_rows(
    tickers_tuple: tuple,
    market: str,
    correction_start_str: str | None,
    jjin_date_str: str | None,
    custom_rs_start_str: str | None = None,
    as_of_date_str: str | None = None,
    swing_dates_str: str | None = None,   # 쉼표 구분 날짜: "2026-05-20,2026-06-08,..."
    # 캐시 무효화 전용 — 종목·지수 스냅샷 fetched_at. 밑줄로 시작하면 st.cache_data가 해시하지 않아
    # 스냅샷이 바뀌어도 30분간 옛 표가 나온다(#32, 20d3cf0과 같은 실수)
    snap_ts: str = '',
    idx_ts: str = '',
) -> list:
    tickers    = list(tickers_tuple)
    index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')
    index_df   = _fetch_index_cached(index_name, idx_ts)

    correction_start  = pd.Timestamp(correction_start_str)  if correction_start_str  else None
    jjin_date         = pd.Timestamp(jjin_date_str)         if jjin_date_str         else None
    custom_peak_date  = pd.Timestamp(custom_rs_start_str)   if custom_rs_start_str   else None
    asof              = pd.Timestamp(as_of_date_str)        if as_of_date_str        else None

    idx_asof = index_df[index_df.index <= asof] if asof is not None else index_df

    adr_min = 2.0 if market.startswith('KR') else 4.0
    swing_dates = [d.strip() for d in swing_dates_str.split(',') if d.strip()] if swing_dates_str else []

    stock_cache = {}
    for ticker in tickers:
        try:
            df = store.load_daily(ticker, market)
            if df.empty or len(df) < 25:
                continue
            df_asof = df[df.index <= asof] if asof is not None else df
            if len(df_asof) < 20:
                continue
            adr = float(((df_asof['High'] - df_asof['Low']) / df_asof['Close']).iloc[-20:].mean() * 100)
            if adr < adr_min:
                continue
            stock_cache[ticker] = (df, round(adr, 2))
        except Exception:
            continue

    # 섹터 = 주력 테마(주도섹터 탭과 같은 기준), 테마 = 세부 표현.
    # 승인된 분류가 없는 종목만 yfinance 산업분류로 섹터를 메운다.
    _tickers = list(stock_cache.keys())
    themes   = get_sectors(_tickers, market)
    sectors  = get_display_sectors(_tickers, market)   # 분류 기록 없는 종목만 yfinance 조회 (#39 M14)
    rows     = []

    for ticker, (df, adr_val) in stock_cache.items():
        try:
            df_asof = df[df.index <= asof] if asof is not None else df
            if df_asof.empty or len(df_asof) < 2:
                continue

            last_close  = float(df_asof['Close'].iloc[-1])
            prev_close  = float(df_asof['Close'].iloc[-2])
            change_pct  = (last_close - prev_close) / prev_close * 100
            name        = get_stock_name(ticker, market)

            if correction_start is not None and not index_df.empty:
                rs = calc_correction_rs(df, index_df, correction_start, jjin_date, custom_peak_date)
            else:
                rs = {
                    'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0, 'excess_adr': 0.0,
                    'lead_days': 0,   'ma_score': 0,
                    'vol_ratio': 0.0, 'candle_ratio': 0.0,
                }

            ma_text, ma_above = _ma_position(df_asof, idx_asof)

            if swing_dates:
                from strategy.swing_grade import calc_swing_grade
                sg = calc_swing_grade(df, swing_dates)
                # F 등급: SMA150 또는 SMA200 아래
                if sg['grade'] != '—':
                    close_now = float(df_asof['Close'].iloc[-1])
                    sma150 = df_asof['Close'].rolling(150).mean().iloc[-1]
                    sma200 = df_asof['Close'].rolling(200).mean().iloc[-1]
                    below_long = (
                        (not pd.isna(sma150) and close_now < sma150) or
                        (not pd.isna(sma200) and close_now < sma200)
                    )
                    if below_long:
                        sg = {'grade': 'F', 'pattern': sg['pattern'], 'score': -1.0}
            else:
                sg = {'grade': '—', 'pattern': '', 'score': 0.0}

            rows.append({
                'Ticker':      ticker,
                '종목명':      name,
                '섹터':        sectors.get(ticker, '-'),
                '테마':        themes.get(ticker, '기타'),
                'ADR':         adr_val,
                'Close':       round(last_close, 2),
                '등락%':       round(change_pct, 2),
                '고점대비%':   calc_pct_from_52w_high(df_asof),
                '저점선행':    rs['lead_days'],
                '조정RS%':     rs['excess_pct'],
                'RS/ADR':      rs['excess_adr'],
                '이평선위치':  ma_text,
                'ma_above_count': ma_above,
                '양봉비%':     None if rs['candle_ratio'] is None else round(rs['candle_ratio'] * 100, 0),
                'RS구간오류':  bool(rs.get('range_error')),     # 기산점 ≥ 찐반등일 (#40 M5)
                '등급':        sg['grade'],
                '패턴':        sg['pattern'],
                '등급점수':    sg['score'],
            })
        except Exception:
            continue

    if swing_dates:
        from strategy.swing_grade import GRADE_ORDER
        rows.sort(key=lambda r: (
            GRADE_ORDER.get(r['등급'], 6),
            -r.get('등급점수', 0),
            -(r['RS/ADR'] or 0),
            -r['ma_above_count'],
            (r['고점대비%'] or 0),
        ))
    else:
        rows.sort(key=lambda r: (
            -(r['RS/ADR'] or 0),
            -r['ma_above_count'],
            (r['고점대비%'] or 0),
        ))
    return rows


def _lead_universe(market: str) -> set | None:
    """주도주 점수(lead_scores) 계산 유니버스 — 주도섹터 탭과 같은 목록. 못 읽으면 None('대상 밖' 판정 생략)."""
    try:
        return {t for t, _ in sector_leadership._universe(sector_leadership.MARKETS[market])}
    except Exception:
        return None


# 주도주 점수·RS/ADR·200일선(모두 lead_scores 값) 칸이 비면 숨은 열의 사유를 회색으로 보여 준다 (#44 M20).
# 정렬·필터는 원래 값 그대로(빈 값).
_GAP_FMT = JsCode("""
function(p) {
    var v = p.value;
    if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) {
        return (p.data && p.data['점수 제외 사유']) || '';
    }
    return String(v);
}
""")
_GAP_STYLE = JsCode("""
function(p) {
    var v = p.value;
    if (v === null || v === undefined || (typeof v === 'number' && isNaN(v))) {
        return {color: 'gray', fontStyle: 'italic'};
    }
    return null;
}
""")


def _status_banner(status: dict, label: str, phase_label: str = 'Normal'):
    state = status['state']
    _DAY_LABELS = ('DAY2', 'DAY3', 'DAY4', 'DAY5', 'DAY6', 'DAY7')

    if state == 'correction':
        cdate  = status['correction_start'].date() if status['correction_start'] else ''
        pdate  = status.get('peak_date')
        failed = status.get('failed_jjin_date')
        parts  = []
        if pdate:
            parts.append(f"RS 기산점: {pdate.date()}")
        parts.append(f"이탈일: {cdate}")
        if failed:
            parts.append(f"{failed.date()} 반등 실패")
        st.warning(f"🔴 **{label} 조정 중** ({' | '.join(parts)})")
    elif phase_label in _DAY_LABELS:
        # DAY2~7: early_signal이든 찐반등 당일 EMA21 즉시 회복이든 동일하게 표시
        vol_stars = '🔥' * status.get('jjin_stars', 0)
        jdate = status['jjin_date'].date() if status.get('jjin_date') else ''
        pdate = status.get('peak_date')
        cdate = status['correction_start'].date() if status.get('correction_start') else ''
        meta_parts = []
        if pdate:
            meta_parts.append(f"RS 기산점: {pdate.date()}")
        if cdate:
            meta_parts.append(f"이탈일: {cdate}")
        meta_parts.append(f"찐반등일: {jdate}")
        meta = f"({' | '.join(meta_parts)})"
        st.success(f"⚡ **{label} {phase_label} 찐반등 감지!** &nbsp; **+{status.get('jjin_pct', 0)}%** &nbsp; {vol_stars}\n\n{meta}")
        rebreak = status.get('rebreak_date')
        if rebreak is not None:
            # 확인창 안 재이탈은 새 조정이 아니다(#28) — 찐반등은 유지하되 주의만 알린다
            st.warning(f"⚠️ **EMA21 재이탈 주의** — {rebreak.date()} 종가가 EMA21 아래로 다시 내려갔습니다 "
                       "(확인창 안이라 찐반등 유지 · DAY7 다음 날에도 EMA21 아래면 이날을 이탈일로 새 조정)")
    else:  # Normal — DAY 윈도우(7일) 완전히 지난 후
        if status.get('correction_start') and not status.get('jjin_date'):
            cdate  = status['correction_start'].date()
            pdate  = status.get('peak_date')
            failed = status.get('failed_jjin_date')
            peak_str = f"RS 기산점: {pdate.date()} | " if pdate else ''
            tail = f"{failed.date()} 반등 실패" if failed else '찐반등 미확인'
            st.warning(f"⚫ **{label} 정상** ({peak_str}이탈일: {cdate} | {tail})")
        elif status.get('jjin_date'):
            jdate = status['jjin_date'].date()
            st.success(f"🟢 **{label} 정상** (찐반등 확인 {jdate})")
        else:
            st.info(f"🟢 **{label} 정상** (21EMA 위)")


def render_watchlist_tab(tickers: list, market: str, label: str):
    # export 섹션을 먼저 비운다 — 아래 어느 경로로 빠져나가든(업로드 없음, 분석 불가)
    # 직전 실행에서 남은 후보가 세션에 살아남아 파일에 실리는 일이 없어야 한다
    tv_export.register_trend(market, [])

    if not tickers:
        st.info(f'사이드바에서 {label} CSV를 업로드해 주세요.')
        return

    with st.expander('사용 가이드', expanded=False):

        # ① DAY 카운팅
        st.caption('DAY 카운팅 & 복귀 조건')
        st.markdown("""
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px">
  <div style="border:1px solid #e74c3c55;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">🔴 DAY1</div>
    <div style="font-size:0.85em;line-height:1.6">EMA21 아래 · 조정 중</div>
    <div style="color:#e74c3c;font-size:0.82em;margin-top:8px">→ 찐반등 봉 출현 대기</div>
  </div>
  <div style="border:1px solid #f1c40f55;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">🟡 DAY2</div>
    <div style="font-size:0.85em;line-height:1.6">찐반등 감지 당일</div>
    <div style="color:#f1c40f;font-size:0.82em;margin-top:8px">→ 주도섹터 탭 차기 주도주 확인</div>
  </div>
  <div style="border:1px solid #3498db55;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">🔵 DAY3~7</div>
    <div style="font-size:0.85em;line-height:1.6">찐반등 이후 1~5 거래일 동안 매수 유효</div>
    <div style="color:#3498db;font-size:0.82em;margin-top:8px">→ 차기 주도주 순위 변화 확인</div>
  </div>
  <div style="border:1px solid #2ecc7155;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">🟢 정상</div>
    <div style="font-size:0.85em;line-height:1.6">EMA21 위 · 찐반등 확인</div>
    <div style="color:#2ecc71;font-size:0.82em;margin-top:8px">→ 보유 유지</div>
  </div>
  <div style="border:1px solid #55555555;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">⚫ 정상</div>
    <div style="font-size:0.85em;line-height:1.6">EMA21 위 · 찐반등 미확인</div>
    <div style="color:gray;font-size:0.82em;margin-top:8px">→ Gray Zone</div>
  </div>
</div>
<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px">
  <div style="border:1px solid #e67e2255;border-radius:8px;padding:10px 14px">
    <div style="font-weight:700;font-size:0.9em;margin-bottom:4px">↩ DAY1 복귀 ①</div>
    <div style="font-size:0.82em;line-height:1.5;color:#e67e22">DAY7 이후 EMA21 미회복 시</div>
  </div>
  <div style="border:1px solid #e67e2255;border-radius:8px;padding:10px 14px">
    <div style="font-weight:700;font-size:0.9em;margin-bottom:4px">↩ DAY1 복귀 ②</div>
    <div style="font-size:0.82em;line-height:1.5;color:#e67e22">찐반등 저점 아래 종가 즉시</div>
  </div>
</div>
""", unsafe_allow_html=True)

        st.divider()

        # ② 찐반등 감지
        st.caption('찐반등일 감지 조건 (3가지 모두 충족)')
        st.markdown(
            '| # | 조건 | 설명 |\n'
            '|---|------|------|\n'
            '| 1 | 장중 저가 EMA21 하방 터치 | 조정 국면임을 확인 |\n'
            '| 2 | 당일 양봉 (종가 > 시가) | 매수세 우위 |\n'
            '| 3 | 상승폭 ≥ ADR 20일 평균 × 0.9 | 평소에 준하는 큰 움직임 |'
        )
        st.caption('거래량 강도 (🔥) — 조정 구간 평균 대비')
        st.markdown("""
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:6px">
  <div style="border:1px solid #e74c3c55;border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:1.3em">🔥🔥🔥</div>
    <div style="font-size:0.82em;margin-top:6px">평균 <b>120%+</b></div>
  </div>
  <div style="border:1px solid #e67e2255;border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:1.3em">🔥🔥</div>
    <div style="font-size:0.82em;margin-top:6px">평균 <b>100~120%</b></div>
  </div>
  <div style="border:1px solid #f1c40f55;border-radius:8px;padding:10px 14px;text-align:center">
    <div style="font-size:1.3em">🔥</div>
    <div style="font-size:0.82em;margin-top:6px">평균 <b>미만</b></div>
  </div>
</div>
<div style="font-size:0.8em;opacity:0.65;margin-top:2px">※ 조정 구간 20거래일 미만이면 20일 이동평균 기준 적용</div>
""", unsafe_allow_html=True)

        st.divider()

        # ③ 핵심 후보
        st.caption('매수 후보')
        st.markdown('매수 후보는 **🧭 주도섹터 탭 → 🎯 차기 주도주**에서 봅니다. 하락국면·상승국면 RS/ADR, '
                    '52주 고점 근접 등 5년 검증으로 정한 배점표가 그 표 아래에 있습니다.')

        st.divider()

        # ④ 컬럼 설명
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 하락국면 RS/ADR | 기산점~찐반등일(전이면 ~기준일) 지수 대비 초과수익 ÷ ADR | 높을수록 강함 |\n'
            '| 상승국면 RS/ADR | 찐반등 전날 종가~기준일 지수 대비 초과수익 ÷ ADR (찐반등 전 빈칸) | 높을수록 강함 |\n'
            '| 200일선 | 200일선 방향(오늘 vs 20거래일 전) + (이격%) | **↗ 상승**이면 주도주 점수 장기 추세 10점 |\n'
            '| 이평선위치 | 지수가 이탈한 이평선(EMA21·SMA50·150·200) 기준 종목 위/아래 | 위가 많을수록 강함 |\n'
            '| 저점선행(일) | 지수 저점보다 N거래일 먼저 저점 형성 | 양수일수록 강함 |\n'
            '| 양봉비% | 양봉 바디 합 / 음봉 바디 합 ×100 | **100 이상** = 매수 우위 |\n'
            '| 고점대비% | 52주 고점 대비 현재 낙폭% | **−30% 이내** 권장 |'
        )
        st.divider()

        # ⑤ Swing Low Grouping
        st.caption('Swing Low Grouping')
        st.markdown("""
<ul style="font-size:0.85em;line-height:1.9;margin-bottom:10px;padding-left:18px">
  <li>지수 저점 날짜를 지정하면, 동일 날짜 기준으로 각 종목의 <b>상승 다이버전스 강도</b>를 등급으로 평가합니다.</li>
  <li>같은 패턴(예: 저→고→저)이어도 <b>실제 가격 수준</b>에 따라 등급이 달라집니다.</li>
</ul>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:10px">
  <div style="border:1px solid #55555530;border-radius:8px;padding:10px 14px">
    <div style="font-weight:700;font-size:0.85em;margin-bottom:4px">① 날짜 선택</div>
    <div style="font-size:0.82em;line-height:1.5;opacity:0.8">지수 저점 날짜를 <b>2개 이상</b> 선택<br>＋/－ 버튼으로 슬롯 추가·제거<br>(최대 8개)</div>
  </div>
  <div style="border:1px solid #55555530;border-radius:8px;padding:10px 14px">
    <div style="font-weight:700;font-size:0.85em;margin-bottom:4px">② 자동 계산</div>
    <div style="font-size:0.82em;line-height:1.5;opacity:0.8">구간별 저가가 구간 내 절대 최저가를<br>갱신했는지 여부로 등급 산정<br>최근 날짜 가중치 4배씩 증가</div>
  </div>
  <div style="border:1px solid #55555530;border-radius:8px;padding:10px 14px">
    <div style="font-weight:700;font-size:0.85em;margin-bottom:4px">③ 등급순 정렬</div>
    <div style="font-size:0.82em;line-height:1.5;opacity:0.8">와치리스트가 자동으로<br>등급 높은 순서로 재정렬</div>
  </div>
</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:6px">
  <div style="border:1px solid #ffd70055;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#FFD700;font-weight:700;font-size:1em">S</div>
    <div style="font-size:0.75em;opacity:0.75;margin-top:3px;line-height:1.4">모든 구간 강한 상승<br>이전 저점 완전 돌파</div>
  </div>
  <div style="border:1px solid #00c87e55;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#00C853;font-weight:700;font-size:1em">A++ ~ A--</div>
    <div style="font-size:0.75em;opacity:0.75;margin-top:3px;line-height:1.4">최근 구간 강세<br>이전 저점 돌파 높음</div>
  </div>
  <div style="border:1px solid #f0a02055;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#f0a020;font-weight:700;font-size:1em">B++ ~ B--</div>
    <div style="font-size:0.75em;opacity:0.75;margin-top:3px;line-height:1.4">일부 구간 상승<br>최근 흐름 혼조</div>
  </div>
  <div style="border:1px solid #e8404055;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#FF1744;font-weight:700;font-size:1em">C</div>
    <div style="font-size:0.75em;opacity:0.75;margin-top:3px;line-height:1.4">모든 구간 신규 저점<br>지수와 동일</div>
  </div>
  <div style="border:1px solid #9e9e9e55;border-radius:8px;padding:8px 10px;text-align:center">
    <div style="color:#9E9E9E;font-weight:700;font-size:1em">F</div>
    <div style="font-size:0.75em;opacity:0.75;margin-top:3px;line-height:1.4">SMA150 또는 SMA200<br>아래 종목 자동 분류</div>
  </div>
</div>
<div style="font-size:0.78em;opacity:0.65;margin-top:8px;padding:6px 10px;border-left:3px solid #55555560;line-height:1.6">
  💡 패턴 레이블: <b>신고</b>(구간 내 절대 최고가 갱신) · <b>고</b>(이전 저가 이상 — 같은 저가 = 쌍바닥도 고, 최고가 미갱신) · <b>저</b>(하락했으나 최저가 유지) · <b>신저</b>(구간 내 절대 최저가 갱신)<br>
  날짜 3개 → 최대 6패턴 &nbsp;|&nbsp; 날짜 4개 이상 → 전 등급(S·A·B·C) 세분화 활성화
</div>
""", unsafe_allow_html=True)

        st.divider()
        if st.button('🔄 재스캔', key=f'rescan_{market}', help='이 시장 시세를 지금 즉시 다시 수집합니다'):
            with st.spinner(f'{label} 재수집 중... ({len(tickers)}개 종목)'):
                store.refetch_market(market, tickers)
                store.refetch_indices()
            _snapshot_ts.clear()    # 새 fetched_at을 바로 읽게 — 10EMA 탭 표도 캐시 키로 함께 갱신된다
            _build_rows.clear()
            _fetch_index_cached.clear()
            _get_market_status_cached.clear()
            st.rerun()

    fr = store.get_freshness(market)
    if fr['fetched_at']:
        st.caption(
            f"📅 {fr['last_trading_date']} 장마감 기준 · "
            f"수집 {fr['fetched_at'].strftime('%m-%d %H:%M')}" + store.lag_note(fr)   # 지연·실패 종목 (#39 M1)
        )
    if fr.get('behind_index'):
        st.warning(f"⚠️ {len(fr.get('lagging') or [])}종목의 데이터가 지수 마지막 날짜({fr['behind_index']})에 못 미칩니다 — "
                   '사이드바 [데이터 재수집]을 눌러 주세요. 그 전 후보는 하루 늦은 데이터로 계산된 것입니다.')
    elif fr.get('index_behind'):   # 반대로 지수가 늦다 (#24)
        st.warning(f"⚠️ {INDEX_FOR_MARKET.get(market, 'NASDAQ')} 지수가 {fr['index_behind']}까지만 있습니다 "
                   f"(종목은 {fr['last_trading_date']}까지) — 사이드바 [데이터 재수집]을 눌러 주세요. "
                   '그 전 국면·DAY·RS는 늦은 지수로 계산된 것입니다.')
    elif fr['is_stale']:
        st.warning('⚠️ 배치 수집이 실패한 것 같습니다 — 사이드바 [데이터 재수집]을 눌러 주세요.')

    snap_ts = _snapshot_ts(market)
    idx_ts  = _snapshot_ts('indices')

    status = _get_market_status_cached(market, idx_ts)

    # 고점 날짜 계산해서 status에 추가 (배너용)
    cs = status['correction_start']
    index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')
    _idx = _fetch_index_cached(index_name, idx_ts)
    if cs:
        status = {**status, 'peak_date': _index_peak_date(_idx, cs) if not _idx.empty else None}

    cs = status['correction_start']
    jd = status['jjin_date']
    correction_start_str = str(cs.date()) if cs else None
    jjin_date_str        = str(jd.date()) if jd else None

    # phase_label 계산 — state='normal'이어도 찐반등이 최근 5거래일 내면 DAY2~7 표시
    _state = status['state']
    if _state == 'correction':
        _phase_label = 'DAY1'
    elif jd is not None and not _idx.empty:
        _days = trading_days_after(_idx, jd)
        _phase_label = f'DAY{min(_days + 2, 7)}' if _days <= 5 else 'Normal'
    else:
        _phase_label = 'Normal'

    # RS 구간 설정 (기산점 + 찐반등일) — 주도섹터 탭과 같은 설정을 공유한다(ui.rs_range, #40 M5).
    # 수동 값은 두 탭 공용 규칙으로 정리해 주도주 점수(lead_scores)에 그대로 넘기고, 이 탭의 RS 구간
    # (저점선행·양봉비)은 주도섹터 탭 detect_phase와 같은 규칙(resolve_rs_range)으로 정한다.
    # 고를 수 있는 마지막 날은 두 탭 모두 지수 최신일 — 같은 범위라야 다른 탭 값이 범위 밖으로 지워지지 않는다(#42 L8)
    auto_peak = status.get('peak_date')
    latest = _idx.index[-1] if not _idx.empty else None
    raw_peak, raw_jjin = rs_range.rs_range_inputs(market, 'trend', auto_peak, jd,
                                                  max_date=latest.date() if latest is not None else None)
    manual_peak, manual_jjin, rs_warns = clean_manual_rs_range(raw_peak, raw_jjin, latest)
    custom_peak, rs_jjin, rs_warn = (resolve_rs_range(_idx.index, manual_peak, manual_jjin, auto_peak, jd)
                                     if not _idx.empty else (None, jd, None))
    rs_range.show_warnings(rs_warns + [rs_warn])
    custom_rs_start_str = rs_range.date_str(custom_peak) or None
    rs_jjin_str         = rs_range.date_str(rs_jjin) or None
    if rs_jjin_str:
        jjin_date_str = rs_jjin_str      # 5분봉 차트도 수동 찐반등일을 따른다

    with st.expander('📊 Swing Low Grouping', expanded=False):
        st.caption('지수 저점 날짜를 선택하면 개별 종목의 상승 다이버전스 등급을 계산합니다.')

        slot_key = f'swing_slot_count_{market}'
        if slot_key not in st.session_state:
            st.session_state[slot_key] = 3
        n_slots = st.session_state[slot_key]

        swing_dates_valid = []
        for row_start in range(0, n_slots, 4):
            cols = st.columns(min(4, n_slots - row_start))
            for j, col in enumerate(cols):
                idx = row_start + j
                with col:
                    d = st.date_input(
                        f'저점 {idx + 1}',
                        value=None,
                        key=f'swing_date_{market}_{idx}',
                    )
                    if d:
                        swing_dates_valid.append(str(d))

        btn_add, btn_del, _ = st.columns([1, 1, 4])
        with btn_add:
            if st.button('＋ 추가', key=f'swing_add_{market}') and n_slots < 8:
                st.session_state[slot_key] += 1
                st.rerun()
        with btn_del:
            if st.button('－ 삭제', key=f'swing_del_{market}') and n_slots > 2:
                last_key = f'swing_date_{market}_{n_slots - 1}'
                st.session_state.pop(last_key, None)
                st.session_state[slot_key] -= 1
                st.rerun()

        if len(swing_dates_valid) >= 2:
            swing_dates_str = ','.join(sorted(set(swing_dates_valid)))
            st.caption(f'🟢 {len(swing_dates_valid)}개 저점 날짜 설정됨 — 등급순 정렬 활성화')
        elif len(swing_dates_valid) == 1:
            swing_dates_str = None
            st.caption('날짜를 2개 이상 선택해야 등급이 계산됩니다.')
        else:
            swing_dates_str = None

    _status_banner(status, label, _phase_label)

    with st.spinner(f'{label} 분석 중... ({len(tickers)}개 종목)'):
        rows = _build_rows(
            tuple(tickers), market,
            correction_start_str, rs_jjin_str,
            custom_rs_start_str, None,
            swing_dates_str,
            snap_ts, idx_ts,
        )

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    # 차기 주도주 점수 — 주도섹터 탭과 같은 계산(같은 함수·같은 캐시)을 그대로 읽는다. 세 화면 일관성.
    # RS 구간 설정(수동 기산점·찐반등일)도 그대로 넘겨 두 탭이 같은 구간으로 계산되게 한다.
    lead = lead_scores(market, rs_range.date_str(manual_peak), rs_range.date_str(manual_jjin))
    for r in rows:
        s = lead.get(r['Ticker'])
        r['주도주점수'] = None if s is None else round(s['score'], 1)
        r['주도주순위'] = None if s is None else s['rank']
        r['하락RS'] = None if s is None or s.get('downAdr') is None else round(s['downAdr'], 2)
        r['상승RS'] = None if s is None or s.get('upAdr') is None else round(s['upAdr'], 2)
        r['200일선'] = None if s is None else s.get('sma200')
    # 점수 대상 밖 종목은 빈칸 대신 사유를 보여 준다 (#44 M20). 값은 채우지 않는다 — _build_rows의 RS/ADR은
    # 구간 정의(get_market_status 기준)가 달라 같은 열에 섞으면 '주도섹터 탭과 같은 값'이 깨진다.
    _tickers_now = [r['Ticker'] for r in rows]
    _low_adr, _adr_min = sector_leadership.lead_low_adr(market, rs_range.date_str(manual_peak),
                                                         rs_range.date_str(manual_jjin))
    gap = lead_gap_reasons(_tickers_now, set(lead), _lead_universe(market),
                           classification.primary_map(market, _tickers_now), _low_adr, _adr_min)
    for r in rows:
        r['점수제외'] = gap.get(r['Ticker'], '')
    # 순위는 이 탭 종목끼리만 다시 매긴다 (점수는 주도섹터 탭과 같고, 순위만 탭 안 기준)
    scored = sorted((r for r in rows if r['주도주점수'] is not None), key=lambda r: -r['주도주점수'])
    for i, r in enumerate(scored, 1):
        r['주도주순위'] = i
    if not swing_dates_str:     # 스윙 등급을 쓸 땐 등급 순서 유지
        rows.sort(key=lambda r: (r['주도주점수'] is None, -(r['주도주점수'] or 0)))

    state = status['state']
    if correction_start_str:
        end_str = rs_jjin_str or '진행 중'
        pdate = status.get('peak_date')
        start_str = custom_rs_start_str or (str(pdate.date()) if pdate else correction_start_str)
        st.caption(f"📅 하락국면 RS 기준기간: {start_str} ~ {end_str}")
    if any(r.get('RS구간오류') for r in rows):
        st.error('**구간 설정 오류** — 기산점이 찐반등일과 같거나 뒤라 하락국면 RS 구간이 비었습니다. '
                 '저점선행·양봉비를 계산하지 않았습니다. ⚙️ RS 구간 설정을 확인해 주세요.')

    show_grade = bool(swing_dates_str)
    crown = leaderboard_store.get_tickers(_LB_MARKET.get(market, 'us'))
    display_df = pd.DataFrame([{
        # 배지는 별도 컬럼 — 티커 문자열에 섞으면 정렬·필터가 그 접두사까지 보게 된다
        '👑': '👑' if r['Ticker'] in crown else '',
        **(({'등급': f"{r['등급']}|{r['패턴']}"} if show_grade else {})),
        '티커 | 종목명': f"{r['Ticker']} | {r['종목명']}",
        '주도주 점수':   r['주도주점수'],
        '주도주 순위':   r['주도주순위'],
        '섹터':          r['섹터'],
        '테마':          r['테마'],
        '종가':          r['Close'],
        '등락%':         r['등락%'],
        '하락국면 RS/ADR': r['하락RS'],
        '상승국면 RS/ADR': r['상승RS'],
        '200일선':       r['200일선'],
        '이평선위치':    r['이평선위치'],
        '저점선행(일)':  r['저점선행'],
        '양봉비%':       r['양봉비%'],
        '고점대비%':     r['고점대비%'],
        '점수 제외 사유': r['점수제외'],      # 숨은 열 — 아래 빈칸 사유 포맷터가 읽는다
    } for r in rows])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True, minWidth=70)
    if market.startswith('KR'):
        close_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        close_fmt = "value == null ? '' : '$' + value.toFixed(2)"
    if show_grade:
        grade_fmt = JsCode("""
function(params) {
    if (!params.value) return '';
    const parts = params.value.split('|');
    return parts[0] + '  ' + (parts[1] || '');
}
""")
        grade_style = JsCode("""
function(params) {
    const grade = params.value ? params.value.split('|')[0] : '';
    const c = {
        S:'#FFD700',
        'A++':'#00E676','A+':'#00C853','A':'#69F0AE','A-':'#40C4FF','A--':'#0091EA',
        'B++':'#E65100','B+':'#F57C00','B':'#FF8F00','B-':'#FFA000','B--':'#FFB300',
        C:'#FF1744',F:'#9E9E9E'
    };
    return {color: c[grade] || 'inherit', fontWeight: 'bold'};
}
""")
        grade_comparator = JsCode("""
function(valueA, valueB) {
    const order = {S:0,'A++':1,'A+':2,A:3,'A-':4,'A--':5,'B++':6,'B+':7,B:8,'B-':9,'B--':10,C:11,F:12};
    const ga = valueA ? valueA.split('|')[0] : '';
    const gb_ = valueB ? valueB.split('|')[0] : '';
    return (order[ga] !== undefined ? order[ga] : 99) - (order[gb_] !== undefined ? order[gb_] : 99);
}
""")
        gb.configure_column('등급', headerName='등급 | 패턴', valueFormatter=grade_fmt,
                            cellStyle=grade_style, comparator=grade_comparator,
                            filter='agTextColumnFilter', flex=2, minWidth=120)
    # 리더보드 배지 — 정렬은 되게(주도주 묶어보기) 두고 폭은 고정한다
    gb.configure_column('👑', headerName='👑', headerTooltip='리더보드 편입 종목',
                        sortable=True, filter=False, floatingFilter=False,
                        resizable=False, suppressSizeToFit=True, flex=0,
                        width=44, minWidth=40, maxWidth=52)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter', flex=2)
    gb.configure_column('주도주 점수', filter='agNumberColumnFilter', type=['numericColumn'], flex=1,
                        valueFormatter=_GAP_FMT, cellStyle=_GAP_STYLE,
                        headerTooltip='주도섹터 탭 차기 주도주와 같은 점수(100) — 배점표는 그 탭 표 아래. '
                                      '계산 대상 밖이면 사유(대상 밖·미분류·데이터 부족)를 표시')
    gb.configure_column('주도주 순위', filter='agNumberColumnFilter', type=['numericColumn'], flex=1,
                        valueFormatter=_GAP_FMT, cellStyle=_GAP_STYLE,
                        headerTooltip='이 탭 종목끼리 매긴 주도주 점수 순위')
    gb.configure_column('섹터', filter='agSetColumnFilter', flex=1)
    gb.configure_column('테마', filter='agSetColumnFilter', flex=1)
    gb.configure_column('종가', filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=close_fmt, flex=1)
    gb.configure_column('등락%',     filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('하락국면 RS/ADR', filter='agNumberColumnFilter', type=['numericColumn'], flex=1,
                        valueFormatter=_GAP_FMT, cellStyle=_GAP_STYLE,
                        headerTooltip='기산점 ~ 찐반등일(찐반등 전이면 ~ 기준일) — 주도섹터 탭과 같은 값')
    gb.configure_column('상승국면 RS/ADR', filter='agNumberColumnFilter', type=['numericColumn'], flex=1,
                        valueFormatter=_GAP_FMT, cellStyle=_GAP_STYLE,
                        headerTooltip='찐반등 전날 종가 ~ 기준일, 찐반등 전엔 빈칸 — 주도섹터 탭과 같은 값')
    gb.configure_column('200일선', filter='agTextColumnFilter', flex=1, minWidth=120,
                        valueFormatter=_GAP_FMT, cellStyle=_GAP_STYLE,     # 이 값도 lead_scores에서 온다 (#44 M20)
                        headerTooltip='200일선 방향(20거래일 전 대비) — 상승이면 주도주 점수 장기 추세 10점. 괄호는 이격')
    gb.configure_column('이평선위치', filter='agTextColumnFilter', flex=2)
    gb.configure_column('저점선행(일)', filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('양봉비%',   filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('고점대비%', filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('점수 제외 사유', hide=True)
    gb.configure_selection('single', use_checkbox=False)
    # 숨은 탭에서 처음 그려지면 폭이 0이라 fit이 안 먹고 오른쪽이 빈다 — 크기가 잡힐 때마다 다시 맞춘다
    _fit = JsCode("function(p){ p.api.sizeColumnsToFit(); }")
    gb.configure_grid_options(domLayout='autoHeight', rowHeight=28, localeText=KO_LOCALE,
                              onGridSizeChanged=_fit, onFirstDataRendered=_fit)

    grid_response = AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
        theme='streamlit',
        fit_columns_on_grid_load=True,
    )

    # 매수 후보 섹션 — 2026-11-10부터 후보 목록을 여기서 보여주지 않고 주도섹터 탭 '차기 주도주'로 일원화.
    # (5년 검증으로 재료·배점을 정리한 쪽이 주도섹터 탭이라 두 곳에 다른 후보가 뜨면 혼란스럽다)
    # TradingView 내보내기의 추세추종 섹션도 비운다 — 화면에 없는 목록을 내보내지 않는다.
    # 다시 보이게 하려면 SHOW_CANDIDATES = True — 후보는 위 '주도주 점수'(주도섹터 탭과 같은 계산) 상위 종목이다.
    _SECTOR_TAB = {'KR_KOSPI': '🧭 코스피 주도섹터', 'KR_KOSDAQ': '🧭 코스닥 주도섹터', 'US': '🧭 나스닥 주도섹터'}
    with st.expander('📋 매수 후보', expanded=True):
        if SHOW_CANDIDATES:
            cands = [r for r in rows if r['주도주점수'] is not None][:CANDIDATE_TOP_N]
            tv_export.register_trend(market, [r['Ticker'] for r in cands])
            st.caption(f'주도주 점수 상위 {len(cands)}개 — 점수·배점은 {_SECTOR_TAB.get(market, "주도섹터")} 탭 차기 주도주와 동일')
            cols = st.columns(3)
            for i, r in enumerate(cands):
                with cols[i % 3], st.container(border=True):
                    st.markdown(f"**{r['Ticker']}** {r['종목명']} · **{r['주도주점수']:.0f}점** ({r['주도주순위']}위)")
                    st.caption(f"🏷 {r['섹터']} | {r['테마']} | ADR {r['ADR']:.1f}% | 52주 고점 {r['고점대비%']:.0f}%")
        else:
            tv_export.register_trend(market, [])
            st.info(f"매수 후보는 **{_SECTOR_TAB.get(market, '주도섹터')}** 탭의 **🎯 차기 주도주**를 참고하세요. "
                    "'추세추종 탭 종목만'을 켜면 이 와치리스트 종목만 볼 수 있어요. "
                    "위 표의 **주도주 점수·순위**도 같은 계산입니다.")

    # 차트 선택은 행 번호가 아니라 티커로 저장하고 위치는 매번 지금 rows에서 다시 찾는다 (#44 M16).
    # 행 번호를 저장하면 종목 수가 줄 때 rows[i]에서 IndexError가 나 뒤의 탭·내보내기까지 멈추고,
    # 정렬이 바뀌면 같은 번호가 다른 종목을 가리킨다.
    sel_key = f'chart_ticker_{market}'
    nav_key = f'chart_nav_{market}'
    row_tickers = [r['Ticker'] for r in rows]

    # 그리드 클릭 → 선택 종목 (prev/next 직후 rerun은 무시)
    if not st.session_state.get(nav_key, False):
        selected_rows = grid_response.get('selected_rows')
        if selected_rows is not None and len(selected_rows) > 0:
            first_row = selected_rows.iloc[0] if isinstance(selected_rows, pd.DataFrame) else selected_rows[0]
            st.session_state[sel_key] = first_row['티커 | 종목명'].split(' | ')[0]
    st.session_state[nav_key] = False

    cur_ticker = st.session_state.get(sel_key)
    cur_idx = chart_position(row_tickers, cur_ticker)
    if cur_idx is None:
        st.session_state[sel_key] = None     # 표에서 빠진 종목 — 선택을 지운다
    index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')

    if cur_idx is not None and jjin_date_str:
        total = len(rows)
        prev_key, next_key = nav_button_keys(market)

        # 스페이스바 → 이 탭의 ▶ (Shift → ◀). 탭마다 자기 버튼만 누른다
        components.html(space_nav_script(market), height=0)

        # 이전/다음 내비게이션
        c_prev, c_info, c_next = st.columns([1, 5, 1])
        with c_prev:
            if st.button('◀', key=prev_key, use_container_width=True):
                st.session_state[nav_key] = True
                st.session_state[sel_key] = chart_step(row_tickers, cur_ticker, -1)
                st.rerun()
        with c_info:
            r_cur = rows[cur_idx]
            st.markdown(
                f"<div style='text-align:center;padding:6px 0'>"
                f"<b>{r_cur['Ticker']}</b> {r_cur['종목명']} &nbsp;"
                f"<span style='color:gray'>{cur_idx + 1} / {total}</span></div>",
                unsafe_allow_html=True,
            )
        with c_next:
            if st.button('▶', key=next_key, use_container_width=True):
                st.session_state[nav_key] = True
                st.session_state[sel_key] = chart_step(row_tickers, cur_ticker, +1)
                st.rerun()

        selected_ticker = cur_ticker
        jjin_date = pd.Timestamp(jjin_date_str)
        with st.spinner('5분봉 로드 중...'):
            stock_5m = _stock_intraday_cached(selected_ticker, jjin_date_str, market)
            index_5m = _index_intraday_cached(index_name, jjin_date_str)

        # 빈 결과는 원인별로 안내한다 — 예전엔 레이트리밋 빈 응답도 '찐반등일이 60일 초과'로 적었다
        empty_reason = intraday_empty_reason(jjin_date, datetime.today(), stock_5m.empty, index_5m.empty,
                                             selected_ticker, index_name)
        if empty_reason is None:
            stock_name = get_stock_name(selected_ticker, market)
            st.plotly_chart(
                intraday_overlay_chart(stock_5m, index_5m, selected_ticker, index_name, jjin_date, market=market, stock_name=stock_name),
                use_container_width=True,
            )
        else:
            st.info(empty_reason)
