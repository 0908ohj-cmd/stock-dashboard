import streamlit as st
from data import store
from strategy.indicators import calc_adr
from strategy.phases import get_phase_label
from strategy.market_status import get_market_status

INDEX_NAMES = ['KOSPI', 'KOSDAQ', 'NASDAQ']

PHASE_COLORS = {
    'DAY1': '🔴',
    'DAY2': '🟡',
    'DAY3': '🔵', 'DAY4': '🔵', 'DAY5': '🔵', 'DAY6': '🔵', 'DAY7': '🔵',
    'Normal': '⚪',
}

PHASE_DESC = {
    'DAY1': '조정 중 (지수 EMA21 아래)',
    'DAY2': '찐반등 감지 → 와치리스트 체크',
    'DAY3': '매수 유효 1일차',
    'DAY4': '매수 유효 2일차',
    'DAY5': '매수 유효 3일차',
    'DAY6': '매수 유효 4일차',
    'DAY7': '매수 유효 마지막 (EMA21 미회복 시 DAY1 복귀)',
    'Normal': '관망',
}


@st.cache_data(ttl=300)
def _load_index(name: str):
    return store.load_index(name)


def render_index_panel():
    st.subheader('지수 현황')
    fr = store.get_freshness('indices')
    if fr['fetched_at']:
        st.caption(
            f"📅 {fr['last_trading_date']} 장마감 기준 · "
            f"수집 {fr['fetched_at'].strftime('%m-%d %H:%M')}"
        )
    cols = st.columns(len(INDEX_NAMES))

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

            # Normal일 때 찐반등 여부로 세분화 — 와치리스트 배너와 일치
            if phase == 'Normal':
                status = get_market_status(df)
                if status.get('jjin_date'):
                    icon = '🟢'
                    desc = f"정상 (찐반등 확인 {status['jjin_date'].date()})"
                elif status.get('correction_start'):
                    icon = '⚫'
                    desc = 'EMA21 회복 (찐반등 미확인)'

            last = float(df['Close'].iloc[-1])
            prev = float(df['Close'].iloc[-2])
            delta_pct = (last - prev) / prev * 100

            st.metric(
                label=f'{icon} {name}',
                value=f'{last:,.2f}',
                delta=f'{delta_pct:+.2f}%',
            )
            st.caption(f'ADR {adr:.2f}%  |  **{phase}** {desc}')
