import streamlit as st
import pandas as pd
import numpy as np
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import fetch_daily, get_stock_name
from data.sector import get_sectors
from strategy.indicators import calc_pct_from_52w_high, calc_ema
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope

CASE_ORDER = {'Case1': 0, 'Case2': 1, '대기중': 2, '하방이탈': 3, '기준봉없음': 4}

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


@st.cache_data(ttl=300)
def _build_10ema_rows(tickers_tuple: tuple, market: str) -> list:
    tickers = list(tickers_tuple)
    rows = []

    for ticker in tickers:
        try:
            df = fetch_daily(ticker, market=market, days=300)
            if df.empty or len(df) < 70:
                continue

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

            rows.append({
                'Ticker':          ticker,
                '종목명':          name,
                '케이스':          case,
                'Close':           round(last_close, 2),
                '등락%':           round(change_pct, 2),
                '기준봉일':        pivot_date_str,
                '기준봉거래량비':  round(pivot_vol_r, 1),
                '횡보일수':        days_since,
                '10EMA기울기%':    round(calc_10ema_slope(df), 2),
                'MA점수':          _ma_score(df, last_close),
                '고점대비%':       calc_pct_from_52w_high(df),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: (
        CASE_ORDER.get(r['케이스'], 99),
        -r['기준봉거래량비'],
    ))
    return rows


def render_10ema_tab(tickers: list, market: str, label: str):
    if not tickers:
        st.info(f'사이드바에서 {label} 파일을 업로드해 주세요.')
        return

    with st.expander('컬럼 설명', expanded=False):
        st.markdown('- **케이스**: Case1(기준봉 범위 횡보 3~15일) / Case2(고점 복귀) / 대기중 / 하방이탈 / 기준봉없음')
        st.markdown('- **기준봉거래량비**: 기준봉 당일 거래량 / 직전 20일 평균 (배수). 클수록 강한 기관 유입')
        st.markdown('- **횡보일수**: 기준봉 이후 현재까지 거래일')
        st.markdown('- **10EMA기울기%**: 최근 5일 EMA10 변화율. 양수 = 우상향')
        st.markdown('- **MA점수**: EMA10/21, SMA50/150/200 위 개수 (0~5). 5 = 완전 정배열')

    with st.spinner(f'{label} 10EMA 분석 중... ({len(tickers)}개 종목)'):
        rows = _build_10ema_rows(tuple(tickers), market)

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    case_counts: dict[str, int] = {}
    for r in rows:
        case_counts[r['케이스']] = case_counts.get(r['케이스'], 0) + 1
    st.caption(' | '.join(f"{k}: {v}개" for k, v in case_counts.items() if v > 0))

    display_df = pd.DataFrame([{
        '티커 | 종목명':   f"{r['Ticker']} | {r['종목명']}",
        '케이스':          r['케이스'],
        'Close':           r['Close'],
        '등락%':           r['등락%'],
        '기준봉일':        r['기준봉일'],
        '기준봉거래량비':  r['기준봉거래량비'],
        '횡보일수':        r['횡보일수'],
        '10EMA기울기%':    r['10EMA기울기%'],
        'MA점수':          r['MA점수'],
        '고점대비%':       r['고점대비%'],
    } for r in rows])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True)
    if market.startswith('KR'):
        close_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        close_fmt = "value == null ? '' : '$' + value.toFixed(2)"
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'],
                         valueFormatter=close_fmt)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter')
    gb.configure_column('케이스', filter='agSetColumnFilter')
    gb.configure_column('기준봉일', filter='agTextColumnFilter')
    for col in ['등락%', '기준봉거래량비', '횡보일수', '10EMA기울기%', 'MA점수', '고점대비%']:
        gb.configure_column(col, filter='agNumberColumnFilter', type=['numericColumn'])
    gb.configure_grid_options(localeText=KO_LOCALE)

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.NO_UPDATE,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=450,
        fit_columns_on_grid_load=False,
    )

    candidates = [r for r in rows if r['케이스'] in ('Case1', 'Case2')]
    if candidates:
        names = [f"**{r['Ticker']}** ({r['케이스']})" for r in candidates]
        st.success('⭐ 매수 대기: ' + ', '.join(names))
