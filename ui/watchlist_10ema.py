import streamlit as st
import pandas as pd
import numpy as np
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

    with st.expander('케이스 & 컬럼 설명', expanded=False):
        st.caption('케이스 분류')
        c1, c2 = st.columns(2)
        c1.markdown(
            '**Case1** — 매수 대기 1순위  \n'
            '기준봉 범위 횡보 3~20일 · 거래량 수축 · 10EMA 우상향\n\n'
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
            '| 케이스 | 현재 진입 단계 분류 | Case1 > Case2 |\n'
            '| 기준봉거래량비 | 기준봉 거래량 / 직전 20일 평균 (배수) | 클수록 강함 |\n'
            '| 횡보일수 | 기준봉 이후 현재까지 거래일 수 | 3~20일 |\n'
            '| 10EMA기울기% | 최근 5일 EMA10 변화율 | 양수 = 우상향 |\n'
            '| MA점수 | EMA10/21 · SMA50/150/200 위 개수 (0~5) | **5** = 완전 정배열 |\n'
            '| 고점대비% | 52주 고점 대비 현재 낙폭% | **−30% 이내** 권장 |'
        )

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
        names = [
            f"**{r['Ticker']}** {r['종목명']} "
            f"({r['케이스']} 거래량비:{r['기준봉거래량비']:.1f}x MA:{r['MA점수']})"
            for r in candidates
        ]
        st.success('⭐ 매수 대기: ' + ', '.join(names))
