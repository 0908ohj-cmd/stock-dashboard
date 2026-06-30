import streamlit as st
from data.fetcher import fetch_index_daily
from strategy.indicators import calc_adr
from strategy.phases import get_phase_label

INDEX_NAMES = ['KOSPI', 'KOSDAQ', 'NASDAQ', 'QQQ']

PHASE_COLORS = {
    'DAY1': '🔴',
    'DAY2': '🟡',
    'DAY3': '🟢', 'DAY4': '🟢', 'DAY5': '🟢',
    'Normal': '⚪',
}

PHASE_DESC = {
    'DAY1': '하락봉 출현',
    'DAY2': '찐반등 감지 → 와치리스트 체크',
    'DAY3': '매수 유효 1일차',
    'DAY4': '매수 유효 2일차',
    'DAY5': '매수 유효 마지막 (EMA21 미회복 시 DAY1 복귀)',
    'Normal': '관망',
}


@st.cache_data(ttl=300)
def _load_index(name: str):
    df = fetch_index_daily(name, days=300)
    return df


def render_index_panel():
    st.subheader('지수 현황')
    cols = st.columns(4)

    for col, name in zip(cols, INDEX_NAMES):
        with col:
            df = _load_index(name)
            if df.empty or len(df) < 3:
                st.metric(name, 'N/A')
                st.caption('데이터 로드 실패')
                continue

            adr = calc_adr(df)
            phase = get_phase_label(df, adr)
            icon = PHASE_COLORS.get(phase, '⚪')
            desc = PHASE_DESC.get(phase, '')

            last = float(df['Close'].iloc[-1])
            prev = float(df['Close'].iloc[-2])
            delta_pct = (last - prev) / prev * 100

            st.metric(
                label=f'{icon} {name}',
                value=f'{last:,.2f}',
                delta=f'{delta_pct:+.2f}%',
            )
            st.caption(f'ADR {adr:.2f}%  |  **{phase}** {desc}')
