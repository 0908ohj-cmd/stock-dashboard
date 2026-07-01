import streamlit as st
import pandas as pd
import numpy as np
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from data.fetcher import (
    fetch_daily, fetch_index_daily, get_stock_name,
    fetch_intraday_for_date, fetch_index_intraday_for_date,
)
from data.sector import get_sectors
from strategy.market_status import get_market_status
from strategy.rs_correction import calc_correction_rs, _index_peak_date
from strategy.indicators import calc_pct_from_52w_high
from ui.intraday_overlay import intraday_overlay_chart

INDEX_FOR_MARKET = {
    'KR_KOSPI':  'KOSPI',
    'KR_KOSDAQ': 'KOSDAQ',
    'US':        'NASDAQ',
}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


@st.cache_data(ttl=300)
def _fetch_index_cached(name: str) -> pd.DataFrame:
    return fetch_index_daily(name, days=400)


@st.cache_data(ttl=300)
def _get_market_status_cached(market: str) -> dict:
    index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')
    index_df   = _fetch_index_cached(index_name)
    return get_market_status(index_df) if not index_df.empty else {
        'state': 'normal', 'correction_start': None,
        'jjin_date': None,  'jjin_pct': 0.0,
        'jjin_stars': 0,    'ftd_date': None,
    }


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


@st.cache_data(ttl=300)
def _build_rows(
    tickers_tuple: tuple,
    market: str,
    correction_start_str: str | None,
    jjin_date_str: str | None,
) -> list:
    tickers    = list(tickers_tuple)
    index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')
    index_df   = _fetch_index_cached(index_name)

    correction_start = pd.Timestamp(correction_start_str) if correction_start_str else None
    jjin_date        = pd.Timestamp(jjin_date_str)        if jjin_date_str        else None

    adr_min = 2.0 if market.startswith('KR') else 4.0
    stock_cache = {}
    for ticker in tickers:
        try:
            df = fetch_daily(ticker, market=market, days=350)
            if df.empty or len(df) < 25:
                continue
            adr = float(((df['High'] - df['Low']) / df['Close']).iloc[-20:].mean() * 100)
            if adr < adr_min:
                continue
            stock_cache[ticker] = (df, round(adr, 2))
        except Exception:
            continue

    sectors = get_sectors(list(stock_cache.keys()), market)
    rows    = []

    for ticker, (df, adr_val) in stock_cache.items():
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

            ma_text, ma_above = _ma_position(df, index_df)
            rows.append({
                'Ticker':      ticker,
                '종목명':      name,
                '섹터':        sectors.get(ticker, '기타'),
                'ADR':         adr_val,
                'Close':       round(last_close, 2),
                '등락%':       round(change_pct, 2),
                '고점대비%':   calc_pct_from_52w_high(df),
                '저점선행':    rs['lead_days'],
                '조정RS%':     rs['excess_pct'],
                'RS/ADR':      rs['excess_adr'],
                '이평선위치':  ma_text,
                'ma_above_count': ma_above,
                '거래량비%':   round(rs['vol_ratio'] * 100, 0),
                '양봉비%':     round(rs['candle_ratio'] * 100, 0),
            })
        except Exception:
            continue

    rows.sort(key=lambda r: (
        -(r['RS/ADR'] or 0),
        -r['ma_above_count'],
        -(r['거래량비%'] or 0),
        (r['고점대비%'] or 0),   # 고점대비% 높을수록(덜 빠진) 우선 → 음수라 오름차순이 유리
    ))
    return rows


def _status_banner(status: dict, label: str):
    state = status['state']
    if state == 'early_signal':
        stars = '⚡' * status['jjin_stars']
        jdate = status['jjin_date'].date() if status['jjin_date'] else ''
        pdate = status.get('peak_date')
        cdate = status['correction_start'].date() if status['correction_start'] else ''
        meta_parts = []
        if pdate:
            meta_parts.append(f"RS 기산점: {pdate.date()}")
        if cdate:
            meta_parts.append(f"이탈일: {cdate}")
        meta_parts.append(f"찐반등일: {jdate}")
        meta = f"({' | '.join(meta_parts)})"
        st.success(f"⚡ **{label} 찐반등 감지!** &nbsp; **+{status['jjin_pct']}%** &nbsp; {stars}\n\n{meta}")
    elif state == 'correction':
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
    else:  # normal
        if status.get('correction_start') and not status.get('jjin_date'):
            cdate = status['correction_start'].date()
            pdate = status.get('peak_date')
            peak_str = f"RS 기산점: {pdate.date()} | " if pdate else ''
            st.warning(f"🟡 **{label} EMA21 회복** ({peak_str}이탈일: {cdate} | 찐반등 미확인)")
        elif status.get('jjin_date'):
            jdate = status['jjin_date'].date()
            st.success(f"✅ **{label} 정상** (찐반등 확인 {jdate})")
        else:
            st.info(f"✅ **{label} 정상** (21EMA 위)")


def render_watchlist_tab(tickers: list, market: str, label: str):
    if not tickers:
        st.info(f'사이드바에서 {label} CSV를 업로드해 주세요.')
        return

    with st.expander('사용 가이드', expanded=False):
        st.caption('핵심 후보')
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown('**① RS/ADR**  \n조정RS%를 ADR로 나눈 정규화 값 · 높을수록 강함')
        c2.markdown('**② 이평선위치**  \n지수 이탈 이평선 기준 종목 위/아래 · 위일수록 강함')
        c3.markdown('**③ 거래량비%**  \n상승일/하락일 평균거래량 비율 · **120 이상** = 매집')
        c4.markdown('**④ 고점대비%**  \n52주 고점 대비 낙폭 · **−30% 이내** 권장')
        st.divider()
        st.caption('DAY 카운팅 & 복귀 조건')
        st.markdown(
            '| 단계 | 내용 |\n'
            '|------|------|\n'
            '| DAY1 | 조정 중 — EMA21 아래, 찐반등 대기 |\n'
            '| DAY2 | 찐반등 감지 당일 |\n'
            '| DAY3~5 | 이후 1~3 거래일 — 매수 유효 구간 |\n'
            '| DAY1 복귀 ① | DAY5 이후 EMA21 미회복 시 |\n'
            '| DAY1 복귀 ② | 찐반등 바디 이상의 음봉 출현 시 즉시 |'
        )
        st.divider()
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 조정RS% | 고점→찐반등 구간 종목수익률 − 지수수익률 | 높을수록 강함 |\n'
            '| RS/ADR | 조정RS%를 ADR로 나눈 정규화 값 | 높을수록 강함 |\n'
            '| 이평선위치 | 지수가 이탈한 이평선(EMA21·SMA50·150·200) 기준 종목 위/아래 | 위가 많을수록 강함 |\n'
            '| 저점선행(일) | 지수 저점보다 N거래일 먼저 저점 형성 | 양수일수록 강함 |\n'
            '| 거래량비% | 상승일 / 하락일 평균거래량 비율 ×100 | **120 이상** = 매집 |\n'
            '| 양봉비% | 양봉 바디 합 / 음봉 바디 합 ×100 | **100 이상** = 매수 우위 |\n'
            '| 고점대비% | 52주 고점 대비 현재 낙폭% | **−30% 이내** 권장 |'
        )

    status = _get_market_status_cached(market)

    # 고점 날짜 계산해서 status에 추가 (배너용)
    cs = status['correction_start']
    if cs:
        index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')
        _idx = _fetch_index_cached(index_name)
        status = {**status, 'peak_date': _index_peak_date(_idx, cs) if not _idx.empty else None}

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

    state = status['state']
    if correction_start_str:
        if state == 'early_signal' and jjin_date_str:
            end_str = '진행 중'
        else:
            end_str = jjin_date_str or '진행 중'
        pdate = status.get('peak_date')
        start_str = str(pdate.date()) if pdate else correction_start_str
        st.caption(f"📅 조정 구간: {start_str} ~ {end_str}")

    display_df = pd.DataFrame([{
        '티커 | 종목명': f"{r['Ticker']} | {r['종목명']}",
        '섹터':          r['섹터'],
        'Close':         r['Close'],
        '등락%':         r['등락%'],
        '조정RS%':       r['조정RS%'],
        'RS/ADR':        r['RS/ADR'],
        '이평선위치':    r['이평선위치'],
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
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter', flex=2)
    gb.configure_column('섹터', filter='agSetColumnFilter', flex=1)
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=close_fmt, flex=1)
    gb.configure_column('등락%',     filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('조정RS%',   filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('RS/ADR',    filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('이평선위치', filter='agTextColumnFilter', flex=2)
    gb.configure_column('저점선행(일)', filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('거래량비%', filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('양봉비%',   filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('고점대비%', filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_selection('single', use_checkbox=False)
    gb.configure_grid_options(domLayout='autoHeight', localeText=KO_LOCALE)

    grid_response = AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        enable_enterprise_modules=False,
        theme='streamlit',
        fit_columns_on_grid_load=True,
    )

    # 핵심 후보 안내
    ma_ok = state == 'normal'  # 지수 정상이면 MA 위치 조건 스킵
    top_candidates = [
        r for r in rows
        if (r['RS/ADR'] or 0) > 0
        and (ma_ok or r['ma_above_count'] > 0)
        and (r['거래량비%'] or 0) >= 120
        and (r['고점대비%'] or 0) >= -30
    ]
    fallback = (
        [r for r in rows
         if (r['RS/ADR'] or 0) > 0
         and (ma_ok or r['ma_above_count'] > 0)
         and (r['고점대비%'] or 0) >= -35][:5]
        if not top_candidates else []
    )

    def _render_candidates(cands: list, section_label: str, period_str: str):
        st.markdown(f'**{section_label}** — {len(cands)}개{period_str}', unsafe_allow_html=True)
        cols = st.columns(3)
        for i, r in enumerate(cands):
            with cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"**{r['Ticker']}** {r['종목명']}")
                    st.caption(f"🏷 {r['섹터']} &nbsp;|&nbsp; ADR {r['ADR']:.1f}%")
                    st.caption(
                        f"RS/ADR: **{r['RS/ADR']:.1f}** &nbsp;|&nbsp; "
                        f"거래량비: **{r['거래량비%']:.0f}%** &nbsp;|&nbsp; "
                        f"고점대비: **{r['고점대비%']:.0f}%**"
                    )
                    st.caption(f"📍 {r['이평선위치']}")

    def _make_period_str(start: str, end: str) -> str:
        return f'  <span style="font-size:0.85em; color:gray;">({start} ~ {end})</span>'

    pdate_c = status.get('peak_date')
    rs_start = str(pdate_c.date()) if pdate_c else correction_start_str

    # 전체 후보 섹션을 하나의 expander로
    has_candidates = bool(top_candidates or fallback)
    has_extra = bool(jjin_date_str)

    if has_candidates or has_extra:
        with st.expander('📋 매수 후보', expanded=True):

            if has_candidates:
                candidates = top_candidates or fallback
                ref_date   = jjin_date_str or str(pd.Timestamp.today().normalize().date())
                cand_label = f'⭐ {ref_date} 기준 핵심 후보' if top_candidates else f'📊 {ref_date} 기준 RS 상위 후보'
                period_str = _make_period_str(rs_start, ref_date) if rs_start else ''
                _render_candidates(candidates, cand_label, period_str)

            # 추가 후보: DAY3·DAY4에만 생성, 이후 동결 유지
            if jjin_date_str:
                jjin_d      = pd.Timestamp(jjin_date_str).date()
                _idx_tmp    = _fetch_index_cached(INDEX_FOR_MARKET.get(market, 'NASDAQ'))
                last_data_d = _idx_tmp.index[-1].date() if not _idx_tmp.empty else pd.Timestamp.today().normalize().date()
                days_since_jjin = max(0, int(np.busday_count(jjin_d, last_data_d)))

                if days_since_jjin >= 1:
                    def _nth_busday(base, n):
                        return str(np.busday_offset(np.datetime64(base, 'D'), n, roll='forward'))

                    day3_date    = _nth_busday(jjin_date_str, 1)
                    day4_date    = _nth_busday(jjin_date_str, 2)
                    core_tickers = {r['Ticker'] for r in (top_candidates or fallback)}

                    with st.spinner('추가 후보 확인 중...'):
                        add_rows = _build_rows(tuple(tickers), market, correction_start_str, None)

                    day3_new = [
                        r for r in add_rows
                        if r['Ticker'] not in core_tickers
                        and (r['RS/ADR'] or 0) > 0
                        and (ma_ok or r['ma_above_count'] > 0)
                        and (r['거래량비%'] or 0) >= 120
                        and (r['고점대비%'] or 0) >= -30
                    ]
                    day3_tickers = {r['Ticker'] for r in day3_new}
                    p3 = _make_period_str(rs_start, day3_date) if rs_start else ''
                    if day3_new:
                        _render_candidates(day3_new, f'⭐ {day3_date} 기준 추가 후보', p3)
                    else:
                        st.markdown(f'**⭐ {day3_date} 기준 추가 후보** — 없음{p3}', unsafe_allow_html=True)

                    if days_since_jjin >= 2:
                        day4_new = [
                            r for r in add_rows
                            if r['Ticker'] not in core_tickers
                            and r['Ticker'] not in day3_tickers
                            and (r['RS/ADR'] or 0) > 0
                            and (ma_ok or r['ma_above_count'] > 0)
                            and (r['거래량비%'] or 0) >= 120
                            and (r['고점대비%'] or 0) >= -30
                        ]
                        p4 = _make_period_str(rs_start, day4_date) if rs_start else ''
                        if day4_new:
                            _render_candidates(day4_new, f'⭐ {day4_date} 기준 추가 후보', p4)
                        else:
                            st.markdown(f'**⭐ {day4_date} 기준 추가 후보** — 없음{p4}', unsafe_allow_html=True)

    selected_rows = grid_response.get('selected_rows')
    if selected_rows is not None and len(selected_rows) > 0:
        first_row = selected_rows.iloc[0] if isinstance(selected_rows, pd.DataFrame) else selected_rows[0]
        selected_ticker = first_row['티커 | 종목명'].split(' | ')[0]
        st.session_state['selected_ticker'] = selected_ticker
        st.session_state['selected_market'] = market
        st.session_state['selected_jjin_date'] = jjin_date_str

        index_name = INDEX_FOR_MARKET.get(market, 'NASDAQ')

        if jjin_date_str:
            jjin_date = pd.Timestamp(jjin_date_str)
            with st.spinner('5분봉 로드 중...'):
                stock_5m = fetch_intraday_for_date(selected_ticker, jjin_date, market=market, days=5)
                index_5m = fetch_index_intraday_for_date(index_name, jjin_date, days=5)

            if not stock_5m.empty and not index_5m.empty:
                st.plotly_chart(
                    intraday_overlay_chart(stock_5m, index_5m, selected_ticker, index_name, jjin_date),
                    use_container_width=True,
                )
            else:
                st.info('5분봉 데이터 없음 (찐반등일이 60일 초과)')


def render_watchlist(kr_kospi: list, kr_kosdaq: list, us_tickers: list):
    st.subheader('와치리스트')
    with st.expander('사용 가이드', expanded=False):
        st.caption('핵심 후보')
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown('**① RS/ADR**  \n조정RS%를 ADR로 나눈 정규화 값 · 높을수록 강함')
        c2.markdown('**② 이평선위치**  \n지수 이탈 이평선 기준 종목 위/아래 · 위일수록 강함')
        c3.markdown('**③ 거래량비%**  \n상승일/하락일 평균거래량 비율 · **120 이상** = 매집')
        c4.markdown('**④ 고점대비%**  \n52주 고점 대비 낙폭 · **−30% 이내** 권장')
        st.divider()
        st.caption('DAY 카운팅 & 복귀 조건')
        st.markdown(
            '| 단계 | 내용 |\n'
            '|------|------|\n'
            '| DAY1 | 조정 중 — EMA21 아래, 찐반등 대기 |\n'
            '| DAY2 | 찐반등 감지 당일 |\n'
            '| DAY3~5 | 이후 1~3 거래일 — 매수 유효 구간 |\n'
            '| DAY1 복귀 ① | DAY5 이후 EMA21 미회복 시 |\n'
            '| DAY1 복귀 ② | 찐반등 바디 이상의 음봉 출현 시 즉시 |'
        )
        st.divider()
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 조정RS% | 고점→찐반등 구간 종목수익률 − 지수수익률 | 높을수록 강함 |\n'
            '| RS/ADR | 조정RS%를 ADR로 나눈 정규화 값 | 높을수록 강함 |\n'
            '| 이평선위치 | 지수가 이탈한 이평선(EMA21·SMA50·150·200) 기준 종목 위/아래 | 위가 많을수록 강함 |\n'
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
