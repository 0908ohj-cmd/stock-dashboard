import streamlit as st
import pandas as pd
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import (
    fetch_daily, fetch_index_daily, get_stock_name,
    fetch_intraday_for_date, fetch_index_intraday_for_date,
)
from data.sector import get_sectors
from strategy.market_status import get_market_status
from strategy.rs_correction import calc_correction_rs
from strategy.indicators import calc_pct_from_52w_high
from ui.intraday_overlay import intraday_overlay_chart

INDEX_FOR_MARKET = {
    'KR_KOSPI':  'KOSPI',
    'KR_KOSDAQ': 'KOSDAQ',
    'US':        'QQQ',
}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


@st.cache_data(ttl=300)
def _fetch_index_cached(name: str) -> pd.DataFrame:
    return fetch_index_daily(name)


@st.cache_data(ttl=300)
def _get_market_status_cached(market: str) -> dict:
    index_name = INDEX_FOR_MARKET.get(market, 'QQQ')
    index_df   = _fetch_index_cached(index_name)
    return get_market_status(index_df) if not index_df.empty else {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None,  'jjin_pct': 0.0,
        'jjin_stars': 0,    'ftd_date': None,
    }


@st.cache_data(ttl=300)
def _build_rows(
    tickers_tuple: tuple,
    market: str,
    correction_start_str: str | None,
    jjin_date_str: str | None,
) -> list:
    tickers    = list(tickers_tuple)
    index_name = INDEX_FOR_MARKET.get(market, 'QQQ')
    index_df   = _fetch_index_cached(index_name)

    correction_start = pd.Timestamp(correction_start_str) if correction_start_str else None
    jjin_date        = pd.Timestamp(jjin_date_str)        if jjin_date_str        else None

    adr_min = 2.0 if market.startswith('KR') else 4.0
    stock_cache = {}
    for ticker in tickers:
        try:
            df = fetch_daily(ticker, market=market)
            if df.empty or len(df) < 25:
                continue
            adr = float(((df['High'] - df['Low']) / df['Close']).iloc[-20:].mean() * 100)
            if adr < adr_min:
                continue
            stock_cache[ticker] = df
        except Exception:
            continue

    sectors = get_sectors(list(stock_cache.keys()), market)
    rows    = []

    for ticker, df in stock_cache.items():
        try:
            last_close  = float(df['Close'].iloc[-1])
            prev_close  = float(df['Close'].iloc[-2])
            change_pct  = (last_close - prev_close) / prev_close * 100
            name        = get_stock_name(ticker, market)

            if correction_start is not None and not index_df.empty:
                rs = calc_correction_rs(df, index_df, correction_start, jjin_date)
            else:
                rs = {
                    'stock_pct': 0.0, 'index_pct': 0.0, 'excess_pct': 0.0, 'excess_adr': 0.0,
                    'lead_days': 0,   'ma_score': 0,
                    'vol_ratio': 0.0, 'candle_ratio': 0.0,
                }

            rows.append({
                'Ticker':    ticker,
                '종목명':    name,
                '섹터':      sectors.get(ticker, '기타'),
                'Close':     round(last_close, 2),
                '등락%':     round(change_pct, 2),
                '고점대비%': calc_pct_from_52w_high(df),
                '저점선행':  rs['lead_days'],
                '조정RS%':   rs['excess_pct'],
                'RS/ADR':    rs['excess_adr'],
                'MA점수':    rs['ma_score'],
                '거래량비%': round(rs['vol_ratio'] * 100, 0),
                '양봉비%':   round(rs['candle_ratio'] * 100, 0),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: (
        -(r['조정RS%'] or 0),
        -r['MA점수'],
        -r['저점선행'],
        -(r['거래량비%'] or 0),
    ))
    return rows


def _status_banner(status: dict, label: str):
    state = status['state']
    if state == 'early_signal':
        stars = '★' * status['jjin_stars']
        jdate = status['jjin_date'].date() if status['jjin_date'] else ''
        st.success(f"⚡ **{label} 찐반등 감지!** {jdate}  +{status['jjin_pct']}%  {stars}")
    elif state == 'correction':
        cdate = status['correction_start'].date() if status['correction_start'] else ''
        st.warning(f"🔴 **{label} 조정 중** (이탈일: {cdate})")
    else:
        st.info(f"✅ **{label} 정상** (21EMA 위)")


def render_watchlist_tab(tickers: list, market: str, label: str):
    if not tickers:
        st.info(f'사이드바에서 {label} CSV를 업로드해 주세요.')
        return

    with st.expander('정렬 기준 & 컬럼 설명', expanded=False):
        st.caption('정렬 순서')
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown('**① 조정RS%**  \n조정 중 지수 대비 초과 수익률')
        c2.markdown('**② MA점수**  \n이평선 구조 건강도 (4+ 권장)')
        c3.markdown('**③ 저점선행(일)**  \n지수보다 먼저 저점 형성')
        c4.markdown('**④ 거래량비%**  \n상승일 거래량 우위 여부')
        st.divider()
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 조정RS% | 고점→찐반등 구간 종목수익률 − 지수수익률 | 높을수록 강함 |\n'
            '| RS/ADR | 조정RS%를 ADR로 나눈 정규화 값 | 높을수록 강함 |\n'
            '| MA점수 | EMA10/21 · SMA50/150/200 위에 있는 개수 (0~5) | **4 이상** 권장 |\n'
            '| 저점선행(일) | 지수 저점보다 N거래일 먼저 저점 형성 | 양수일수록 강함 |\n'
            '| 거래량비% | 상승일 / 하락일 평균거래량 비율 ×100 | **120 이상** = 매집 |\n'
            '| 양봉비% | 양봉 바디 합 / 음봉 바디 합 ×100 | **100 이상** = 매수 우위 |\n'
            '| 고점대비% | 52주 고점 대비 현재 낙폭% | **−30% 이내** 권장 |'
        )

    status = _get_market_status_cached(market)
    _status_banner(status, label)

    cs = status['correction_start']
    jd = status['jjin_date']
    correction_start_str = str(cs.date()) if cs else None
    jjin_date_str        = str(jd.date()) if jd else None

    with st.spinner(f'{label} 분석 중... ({len(tickers)}개 종목)'):
        rows = _build_rows(
            tuple(tickers), market,
            correction_start_str, jjin_date_str,
        )

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    if correction_start_str:
        end_str = jjin_date_str or '진행 중'
        st.caption(f"📅 조정 구간: {correction_start_str} ~ {end_str}")

    display_df = pd.DataFrame([{
        '티커 | 종목명': f"{r['Ticker']} | {r['종목명']}",
        '섹터':          r['섹터'],
        'Close':         r['Close'],
        '등락%':         r['등락%'],
        '조정RS%':       r['조정RS%'],
        'RS/ADR':        r['RS/ADR'],
        'MA점수':        r['MA점수'],
        '저점선행(일)':  r['저점선행'],
        '거래량비%':     r['거래량비%'],
        '양봉비%':       r['양봉비%'],
        '고점대비%':     r['고점대비%'],
    } for r in rows])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True, minWidth=70)
    if market.startswith('KR'):
        close_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        close_fmt = "value == null ? '' : '$' + value.toFixed(2)"
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter', width=180)
    gb.configure_column('섹터', filter='agSetColumnFilter', width=110)
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=close_fmt, width=100)
    gb.configure_column('등락%',     filter='agNumberColumnFilter', type=['numericColumn'], width=75)
    gb.configure_column('조정RS%',   filter='agNumberColumnFilter', type=['numericColumn'], width=82)
    gb.configure_column('RS/ADR',    filter='agNumberColumnFilter', type=['numericColumn'], width=78)
    gb.configure_column('MA점수',    filter='agNumberColumnFilter', type=['numericColumn'], width=78)
    gb.configure_column('저점선행(일)', filter='agNumberColumnFilter', type=['numericColumn'], width=95)
    gb.configure_column('거래량비%', filter='agNumberColumnFilter', type=['numericColumn'], width=85)
    gb.configure_column('양봉비%',   filter='agNumberColumnFilter', type=['numericColumn'], width=80)
    gb.configure_column('고점대비%', filter='agNumberColumnFilter', type=['numericColumn'], width=85)
    gb.configure_selection('single', use_checkbox=False)
    gb.configure_grid_options(localeText=KO_LOCALE)

    grid_opts = gb.build()
    for col_def in grid_opts.get('columnDefs', []):
        col_def['suppressSizeToFit'] = True

    grid_height = 90 + len(display_df) * 36

    grid_response = AgGrid(
        display_df,
        gridOptions=grid_opts,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=grid_height,
        fit_columns_on_grid_load=False,
    )

    # 핵심 후보 안내
    top_candidates = [
        r for r in rows
        if (r['조정RS%'] or 0) >= 10
        and r['MA점수'] >= 4
        and (r['고점대비%'] or 0) >= -30
    ]
    if top_candidates:
        names = [
            f"**{r['Ticker']}** {r['종목명']} "
            f"(RS:{r['조정RS%']:.0f}% MA:{r['MA점수']} 선행:{r['저점선행']}일)"
            for r in top_candidates
        ]
        st.success(
            f"⭐ 핵심 후보 (조정RS% ≥10% & MA점수 4+ & 고점대비 -30% 이내): "
            + ", ".join(names)
        )
    elif rows:
        # 단기 조정 등으로 엄격 기준 미달 시 → MA점수 3+ & 고점대비 -35% 이내 상위 5개
        relaxed = [r for r in rows if r['MA점수'] >= 3 and (r['고점대비%'] or 0) >= -35][:5]
        if relaxed:
            names = [
                f"**{r['Ticker']}** {r['종목명']} "
                f"(RS:{r['조정RS%']:.0f}% MA:{r['MA점수']})"
                for r in relaxed
            ]
            st.info("📊 RS 상위 후보 (단기 조정 — 완화 기준): " + ", ".join(names))

    selected_rows = grid_response.get('selected_rows')
    if selected_rows is not None and len(selected_rows) > 0:
        first_row = selected_rows.iloc[0] if isinstance(selected_rows, pd.DataFrame) else selected_rows[0]
        selected_ticker = first_row['티커 | 종목명'].split(' | ')[0]
        st.session_state['selected_ticker'] = selected_ticker
        st.session_state['selected_market'] = market
        st.session_state['selected_jjin_date'] = jjin_date_str

        index_name = INDEX_FOR_MARKET.get(market, 'QQQ')

        if jjin_date_str:
            jjin_date = pd.Timestamp(jjin_date_str)
            st.markdown(f'**{selected_ticker} — 찐반등 날 5분봉 비교** ({jjin_date.date()})')
            with st.spinner('5분봉 로드 중...'):
                stock_5m = fetch_intraday_for_date(selected_ticker, jjin_date, market=market)
                index_5m = fetch_index_intraday_for_date(index_name, jjin_date)

            if not stock_5m.empty and not index_5m.empty:
                st.plotly_chart(
                    intraday_overlay_chart(stock_5m, index_5m, selected_ticker, index_name),
                    use_container_width=True,
                )
            else:
                st.info('5분봉 데이터 없음 (찐반등일이 60일 초과)')


def render_watchlist(kr_kospi: list, kr_kosdaq: list, us_tickers: list):
    st.subheader('와치리스트')
    with st.expander('정렬 기준 & 컬럼 설명', expanded=False):
        st.caption('정렬 순서')
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown('**① 조정RS%**  \n조정 중 지수 대비 초과 수익률')
        c2.markdown('**② MA점수**  \n이평선 구조 건강도 (4+ 권장)')
        c3.markdown('**③ 저점선행(일)**  \n지수보다 먼저 저점 형성')
        c4.markdown('**④ 거래량비%**  \n상승일 거래량 우위 여부')
        st.divider()
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 조정RS% | 고점→찐반등 구간 종목수익률 − 지수수익률 | 높을수록 강함 |\n'
            '| RS/ADR | 조정RS%를 ADR로 나눈 정규화 값 | 높을수록 강함 |\n'
            '| MA점수 | EMA10/21 · SMA50/150/200 위에 있는 개수 (0~5) | **4 이상** 권장 |\n'
            '| 저점선행(일) | 지수 저점보다 N거래일 먼저 저점 형성 | 양수일수록 강함 |\n'
            '| 거래량비% | 상승일 / 하락일 평균거래량 비율 ×100 | **120 이상** = 매집 |\n'
            '| 양봉비% | 양봉 바디 합 / 음봉 바디 합 ×100 | **100 이상** = 매수 우위 |\n'
            '| 고점대비% | 52주 고점 대비 현재 낙폭% | **−30% 이내** 권장 |'
        )

    tab_kospi, tab_kosdaq, tab_us = st.tabs(['🇰🇷 KOSPI', '🇰🇷 KOSDAQ', '🇺🇸 US'])
    with tab_kospi:
        render_watchlist_tab(kr_kospi,  'KR_KOSPI',  'KOSPI')
    with tab_kosdaq:
        render_watchlist_tab(kr_kosdaq, 'KR_KOSDAQ', 'KOSDAQ')
    with tab_us:
        render_watchlist_tab(us_tickers, 'US', 'US')
