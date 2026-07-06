import streamlit as st
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import fetch_daily, get_stock_name
from data.sector import get_sectors
from strategy.indicators import calc_pct_from_52w_high, calc_ema
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope

CASE_ORDER = {'Case1': 0, 'Case2': 1, '대기중': 2, '중간선이탈': 3, '10EMA이탈': 4, '하방이탈': 5, '기준봉없음': 6}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


def _ma_score(df: pd.DataFrame, close: float) -> int:
    mas = [
        calc_ema(df, 10).iloc[-1],
        calc_ema(df, 21).iloc[-1],
        df['Close'].rolling(50).mean().iloc[-1],
        df['Close'].rolling(150).mean().iloc[-1],
        df['Close'].rolling(200).mean().iloc[-1],
    ]
    return sum(1 for v in mas if not pd.isna(v) and close > v)


def _process_one(ticker: str, market: str) -> dict | None:
    try:
        df = fetch_daily(ticker, market=market, days=300)
        if df.empty or len(df) < 70:
            return None

        pivot  = find_pivot_candle(df)
        case   = classify_case(df, pivot)
        name   = get_stock_name(ticker, market)

        last_close = float(df['Close'].iloc[-1])
        prev_close = float(df['Close'].iloc[-2])
        change_pct = (last_close - prev_close) / prev_close * 100

        pivot_date_str = str(pivot['date'].date()) if pivot else ''
        pivot_vol_r    = pivot['vol_ratio'] if pivot else 0.0
        days_since     = (
            int(np.busday_count(pivot['date'].date(), df.index[-1].date()))
            if pivot else 0
        )

        # 타점 / 손절 / 리스크 / 이전상승%
        if pivot:
            entry_price    = round(pivot['high'], 2)
            stop_price     = round(pivot['midline'], 2)
            risk_pct       = round((entry_price - stop_price) / entry_price * 100, 1)
            near_entry_pct = round((last_close / entry_price - 1) * 100, 2)
            # 기준봉 이전 65거래일 내 저점 → 기준봉 고가 상승률 (쿨라매기 Prior Move 확인용)
            pivot_pos  = df.index.get_loc(pivot['date'])
            start_pos  = max(0, pivot_pos - 65)
            prior_low  = float(df['Low'].iloc[start_pos:pivot_pos].min()) if pivot_pos > 0 else 0.0
            prior_move_pct = round((pivot['high'] / prior_low - 1) * 100, 0) if prior_low > 0 else 0.0
        else:
            entry_price = stop_price = risk_pct = near_entry_pct = prior_move_pct = 0.0

        return {
            'Ticker':          ticker,
            '종목명':          name,
            '케이스':          case,
            'Close':           round(last_close, 2),
            '타점(기준봉고가)': entry_price,
            '손절(중간선)':    stop_price,
            '리스크%':         risk_pct,
            '현재→타점%':      near_entry_pct,
            '이전상승%':       prior_move_pct,
            '등락%':           round(change_pct, 2),
            '기준봉일':        pivot_date_str,
            '기준봉거래량비':  round(pivot_vol_r, 1),
            '횡보일수':        days_since,
            '10EMA기울기%':    round(calc_10ema_slope(df), 2),
            'MA점수':          _ma_score(df, last_close),
            '고점대비%':       calc_pct_from_52w_high(df),
        }
    except Exception:
        return None


_ROW_SCHEMA_VER = 2  # 컬럼 구조 변경 시 증가 → 구캐시 자동 무효화

@st.cache_data(ttl=3600)
def _build_10ema_rows(tickers_tuple: tuple, market: str, schema_ver: int = _ROW_SCHEMA_VER) -> list:
    tickers = list(tickers_tuple)
    rows = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_process_one, t, market): t for t in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                rows.append(result)

    rows.sort(key=lambda r: (
        CASE_ORDER.get(r['케이스'], 99),
        -r['기준봉거래량비'],
    ))
    return rows


def render_10ema_tab(market: str, label: str):
    from data.universe import get_kr_universe, get_us_universe

    # 유니버스 로드 (24h 캐시)
    with st.spinner(f'{label} 유니버스 로딩 중...'):
        if market == 'US':
            tickers = get_us_universe()
        else:
            tickers = get_kr_universe(market)

    if not tickers:
        st.warning('유니버스를 불러오지 못했습니다. 잠시 후 새로고침 해주세요.')
        return

    with st.expander('케이스 & 컬럼 설명', expanded=False):
        st.caption('케이스 분류')
        c1, c2 = st.columns(2)
        c1.markdown(
            '**Case1** — 매수 대기 1순위  \n'
            '기준봉 범위 횡보 3~40일 · 베이스 범위 ≤15% · 거래량 수축 · 10EMA 우상향\n\n'
            '**Case2** — 매수 대기 2순위  \n'
            '돌파 후 +10% 이내 · 5일 이내에 기준봉 고가 부근 복귀\n\n'
            '**대기중** — 조건 미충족, 아직 진행 중'
        )
        c2.markdown(
            '**중간선이탈** — 기준봉 (고+저)/2 아래 터치 → 영구 탈락  \n\n'
            '**10EMA이탈** — 10EMA 아래 연속 2거래일 → 영구 탈락  \n\n'
            '**하방이탈** — 기준봉 저가 아래  \n\n'
            '**기준봉없음** — 최근 63일 내 기준봉 미탐지'
        )
        st.divider()
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 타점(기준봉고가) | **매수 진입가** — 이 가격 돌파 시 ORH 매수 | 기준봉 고가 |\n'
            '| 손절(중간선) | 기준봉 (고+저)/2 — 이탈 시 손절 기준 | 중간선 아래 = 셋업 무효 |\n'
            '| 리스크% | (타점 − 손절) / 타점 — 타점 진입 시 최대 손실 % | 작을수록 유리 |\n'
            '| 현재→타점% | 현재가 / 타점 − 1 (음수 = 타점 아래, 0에 가까울수록 임박) | −5% 이내 주목 |\n'
            '| 이전상승% | 기준봉 이전 3개월 내 저점→기준봉 고가 상승폭 (쿨라매기 Prior Move) | **30%+** 필수 |\n'
            '| 케이스 | 현재 진입 단계 분류 | Case1 > Case2 |\n'
            '| 기준봉거래량비 | 기준봉 거래량 / 직전 20일 평균 (배수) | 클수록 강함 |\n'
            '| 횡보일수 | 기준봉 이후 현재까지 거래일 수 | 3~40일 |\n'
            '| 10EMA기울기% | 최근 5일 EMA10 변화율 | 양수 = 우상향 |\n'
            '| MA점수 | EMA10/21 · SMA50/150/200 위 개수 (0~5) | **5** = 완전 정배열 |\n'
            '| 고점대비% | 52주 고점 대비 현재 낙폭% | **−30% 이내** 권장 |'
        )
        st.divider()
        if st.button('🔄 재스캔', key=f'rescan_10ema_{market}', help='전 종목 재스캔 — 수 분 소요'):
            _build_10ema_rows.clear()
            st.rerun()

    with st.spinner(f'{label} 스캔 중... {len(tickers)}개 종목 (첫 로드 시 수 분 소요)'):
        rows = _build_10ema_rows(tuple(sorted(tickers)), market, _ROW_SCHEMA_VER)

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    # 케이스 요약
    case_counts: dict = {}
    for r in rows:
        case_counts[r['케이스']] = case_counts.get(r['케이스'], 0) + 1
    st.caption(
        f'스캔: {len(tickers)}개 종목  |  '
        + ' | '.join(f"{k}: {v}개" for k, v in case_counts.items() if v > 0)
    )

    # Case1/Case2만 기본 표시, 전체 보기 옵션
    show_all = st.checkbox('전체 종목 보기', value=False, key=f'show_all_{market}')
    if show_all:
        display_rows = rows
    else:
        display_rows = [r for r in rows if r['케이스'] in ('Case1', 'Case2')]
        if not display_rows:
            st.info('현재 Case1/Case2 해당 종목 없음. "전체 종목 보기"를 체크하면 전체를 확인할 수 있습니다.')
            return

    display_df = pd.DataFrame([{
        '티커 | 종목명':    f"{r['Ticker']} | {r['종목명']}",
        '케이스':           r['케이스'],
        'Close':            r['Close'],
        '타점(기준봉고가)': r['타점(기준봉고가)'],
        '손절(중간선)':     r['손절(중간선)'],
        '리스크%':          r['리스크%'],
        '현재→타점%':       r['현재→타점%'],
        '이전상승%':        r['이전상승%'],
        '등락%':            r['등락%'],
        '기준봉일':         r['기준봉일'],
        '기준봉거래량비':   r['기준봉거래량비'],
        '횡보일수':         r['횡보일수'],
        '10EMA기울기%':     r['10EMA기울기%'],
        'MA점수':           r['MA점수'],
        '고점대비%':        r['고점대비%'],
    } for r in display_rows])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True)
    if market.startswith('KR'):
        close_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        close_fmt = "value == null ? '' : '$' + value.toFixed(2)"
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'],
                         valueFormatter=close_fmt)
    gb.configure_column('타점(기준봉고가)', filter='agNumberColumnFilter', type=['numericColumn'],
                         valueFormatter=close_fmt)
    gb.configure_column('손절(중간선)', filter='agNumberColumnFilter', type=['numericColumn'],
                         valueFormatter=close_fmt)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter')
    gb.configure_column('케이스', filter='agSetColumnFilter')
    gb.configure_column('기준봉일', filter='agTextColumnFilter')
    for col in ['리스크%', '현재→타점%', '이전상승%', '등락%', '기준봉거래량비', '횡보일수', '10EMA기울기%', 'MA점수', '고점대비%']:
        gb.configure_column(col, filter='agNumberColumnFilter', type=['numericColumn'])
    gb.configure_grid_options(localeText=KO_LOCALE)

    grid_height = min(90 + len(display_df) * 36, 800)

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.NO_UPDATE,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=grid_height,
        fit_columns_on_grid_load=False,
    )

    candidates = [r for r in display_rows if r['케이스'] in ('Case1', 'Case2')]
    if candidates:
        names = [
            f"**{r['Ticker']}** {r['종목명']} "
            f"({r['케이스']} | 타점:{r['타점(기준봉고가)']} | 리스크:{r['리스크%']}% | MA:{r['MA점수']})"
            for r in candidates
        ]
        st.success('⭐ 매수 대기 (타점 = 기준봉 고가 돌파 ORH): ' + '  \n'.join(names))
