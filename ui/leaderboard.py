"""👑 리더보드 섹션 — 시장 주도주 명단 열람.

data/leaderboard/*.json만 읽는다 (네트워크 수집 없음).

⚠️ 이 섹션은 와치리스트 탭 그룹보다 **앞에서** 렌더해야 한다. Streamlit은 상호작용마다
app.py를 위에서 아래로 통째로 재실행하고, `st.tabs()`는 탭 모양만 만들 뿐 각 탭 본문을
스크립트 순서대로 실행한다. 리더보드가 탭 그룹 뒤에 있으면 앞 탭들이 수백 종목 시세를
받는 동안(캐시가 비면 10분 이상) 이 코드 줄에 도달하지 못해, 탭은 보이는데 안이 빈
화면이 된다. 렌더 자체는 파일 읽기라 즉시 끝나므로 순서만 앞에 두면 바로 표시된다.
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
    """내부 스크리너 슬러그는 표시용 라벨로만 노출한다.

    매핑에 없는 슬러그를 그대로 찍으면 내부 명명이 사용자 화면으로 새어나간다 —
    중립적인 '기타'로 접고 중복은 제거한다.
    """
    labels = []
    for s in (sources or []):
        label = _SOURCE_LABEL.get(s, '기타')
        if label not in labels:
            labels.append(label)
    return ', '.join(labels)


def _fmt_market(market) -> str:
    """KR 아이템의 시장 구분(KOSPI/KOSDAQ) 표시값.

    소스에 시장이 없으면 동기화가 'KR'로 메워 보낸다 — 구분을 아는 척하지 않고
    그대로 노출한다. 문자열이 아닌 값도 표에서 터지지 않게 '—'로 접는다.
    """
    if not isinstance(market, str) or not market:
        return '—'
    return market


def _fmt_updated(iso: str) -> str:
    """'2026-07-29T07:02:16' → '07-29 07:02'"""
    if not iso:
        return '—'
    try:
        return f'{iso[5:7]}-{iso[8:10]} {iso[11:16]}'
    except Exception:
        return iso


def render_leaderboard_section(expanded: bool = False):
    """접을 수 있는 리더보드 섹션. 와치리스트 탭 그룹보다 앞에서 호출한다.

    기본은 접힌 상태다. 대신 **접힌 제목에 지연 여부를 함께 노출**한다 —
    펼쳐야만 보이는 경고는 접어둔 사람에게 도달하지 않고, 표가 그럴듯하게
    채워져 있으면 오래된 데이터를 최신으로 오인하기 쉽기 때문이다.
    """
    label = '👑 리더보드 — 시장 주도주 명단'
    stale = [m.upper() for m in ('us', 'kr')
             if leaderboard_store.get_freshness(m)['is_stale']]
    if stale:
        label += f"  ⚠️ 갱신 지연 ({'/'.join(stale)})"

    with st.expander(label, expanded=expanded):
        _render_body()


def _render_body():
    st.caption('시장 전체에서 RS 상위 주도주를 매일 추려낸 명단입니다.')

    choice = st.radio('시장', ['🇺🇸 US', '🇰🇷 KR'],
                      horizontal=True, key='lb_market', label_visibility='collapsed')
    market = 'us' if 'US' in choice else 'kr'

    # 데이터 유무는 파일 존재로만 판단한다 — 갱신 시각이 없거나 깨져도
    # items는 멀쩡할 수 있고, 그 경우 와치리스트 탭엔 👑가 붙는데 여기만 비면 모순이다
    if not leaderboard_store.has_snapshot(market):
        st.info('리더보드 데이터가 아직 없습니다.')
        return

    data = leaderboard_store.load(market)
    fresh = leaderboard_store.get_freshness(market)
    items = data.get('items', [])

    c1, c2 = st.columns([3, 1])
    c1.caption(f"📅 {_fmt_updated(data.get('source_updated_at'))} 갱신 · {len(items)}종목")
    if fresh['is_stale']:
        c2.warning('갱신 지연', icon='⚠️')

    if not items:
        st.info('조건을 충족한 종목이 없습니다 — 주도주 부재 신호일 수 있습니다.')
        return

    is_kr = market == 'kr'
    display_df = pd.DataFrame([{
        '#':        it.get('rank'),        # 구 스키마·수기 편집 파일엔 없을 수 있다
        '티커 | 종목명': f"{it.get('ticker')} | {it['name']}" if it.get('name') else it.get('ticker'),
        # 시장 구분은 KR에서만 의미가 있다 — US는 전 종목이 'US'라 컬럼이 노이즈다
        **({'시장': _fmt_market(it.get('market'))} if is_kr else {}),
        '소스':      _fmt_sources(it.get('sources')),
        # 섹터(무슨 업인가)와 테마(왜 지금 사는가)는 별개 축이다. 둘 다 상류
        # 파이프라인이 만들어 보낸 값을 그대로 표시만 한다 — 여기서 재분류하지 않는다.
        '섹터':      it.get('sector') or '-',
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
    if is_kr:
        gb.configure_column('시장', filter='agSetColumnFilter', maxWidth=110)
    gb.configure_column('소스', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('섹터', filter='agSetColumnFilter', minWidth=110, flex=1)
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
