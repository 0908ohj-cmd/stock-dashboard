"""👑 리더보드 탭 — 시장 주도주 명단 열람.

data/leaderboard/*.json만 읽는다 (네트워크 수집 없음).
"""
import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

from data import leaderboard_store

_SOURCE_LABEL = {
    'leaders': '리더',
    'leader_ride': '리더 10EMA',
}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


def _fmt_sources(sources) -> str:
    return ', '.join(_SOURCE_LABEL.get(s, s) for s in (sources or []))


def _fmt_updated(iso: str) -> str:
    """'2026-07-29T07:02:16' → '07-29 07:02'"""
    if not iso:
        return '—'
    try:
        return f'{iso[5:7]}-{iso[8:10]} {iso[11:16]}'
    except Exception:
        return iso


def render_leaderboard_tab():
    st.markdown('#### 👑 리더보드')
    st.caption('시장 전체에서 RS 상위 주도주를 매일 추려낸 명단입니다.')

    choice = st.radio('시장', ['🇺🇸 US', '🇰🇷 KR'],
                      horizontal=True, key='lb_market', label_visibility='collapsed')
    market = 'us' if 'US' in choice else 'kr'

    data = leaderboard_store.load(market)
    fresh = leaderboard_store.get_freshness(market)
    items = data.get('items', [])

    if not fresh['has_data']:
        st.info('리더보드 데이터가 아직 없습니다.')
        return

    c1, c2 = st.columns([3, 1])
    c1.caption(f"📅 {_fmt_updated(data.get('source_updated_at'))} 갱신 · {len(items)}종목")
    if fresh['is_stale']:
        c2.warning('갱신 지연', icon='⚠️')

    if not items:
        st.info('조건을 충족한 종목이 없습니다 — 주도주 부재 신호일 수 있습니다.')
        return

    is_kr = market == 'kr'
    display_df = pd.DataFrame([{
        '#':        it['rank'],
        '티커 | 종목명': f"{it['ticker']} | {it['name']}" if it.get('name') else it['ticker'],
        '소스':      _fmt_sources(it.get('sources')),
        '테마':      it.get('theme') or '기타',
        '종가':      it.get('close'),
        'RS':        it.get('rs_rating'),
        'ADR%':      it.get('adr'),
        '1M%':       it.get('perf_1m'),
        '3M%':       it.get('perf_3m'),
        '6M%':       it.get('perf_6m'),
        '12M%':      it.get('perf_12m'),
        '52H%':      it.get('dist_from_52w_high'),
        '거래대금':   it.get('avg_dollar_vol'),
    } for it in items])

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True,
                                floatingFilter=True, minWidth=70)

    close_fmt = ("value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
                 if is_kr else "value == null ? '' : '$' + value.toFixed(2)")
    vol_fmt = JsCode("""
function(params) {
    const v = params.value;
    if (v == null) return '';
    if (%s) {
        if (v >= 1e12) return (v/1e12).toFixed(1) + '조';
        if (v >= 1e8)  return Math.round(v/1e8).toLocaleString('ko-KR') + '억';
        return Math.round(v).toLocaleString('ko-KR');
    }
    if (v >= 1e9) return '$' + (v/1e9).toFixed(1) + 'B';
    if (v >= 1e6) return '$' + Math.round(v/1e6) + 'M';
    return '$' + Math.round(v).toLocaleString();
}
""" % ('true' if is_kr else 'false'))
    rs_style = JsCode("""
function(params) {
    if (params.value == null) return {};
    return params.value >= 80
        ? {color: '#00E676', fontWeight: 'bold'}
        : {};
}
""")

    gb.configure_column('#', type=['numericColumn'], maxWidth=70)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter',
                        pinned='left', minWidth=160, flex=2)
    gb.configure_column('소스', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('테마', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('종가', filter='agNumberColumnFilter', type=['numericColumn'],
                        valueFormatter=close_fmt, flex=1)
    gb.configure_column('RS', filter='agNumberColumnFilter', type=['numericColumn'],
                        cellStyle=rs_style, maxWidth=90)
    for col in ('ADR%', '1M%', '3M%', '6M%', '12M%', '52H%'):
        gb.configure_column(col, filter='agNumberColumnFilter',
                            type=['numericColumn'], flex=1)
    gb.configure_column('거래대금', filter='agNumberColumnFilter',
                        type=['numericColumn'], valueFormatter=vol_fmt, flex=1)
    gb.configure_grid_options(domLayout='autoHeight', rowHeight=28, localeText=KO_LOCALE)

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        enable_enterprise_modules=False,
        allow_unsafe_jscode=True,
        theme='streamlit',
        fit_columns_on_grid_load=True,
    )
