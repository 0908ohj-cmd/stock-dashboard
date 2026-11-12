import streamlit as st
from data import live_index, store
from strategy.indicators import calc_adr, calc_ema
from strategy.phases import get_phase_label
from strategy.market_status import get_market_status


def _pp_ok(df) -> bool:
    """PP 거래 가능 조건: 지수 10EMA > 21EMA, 두 EMA 모두 기울기 양수 (5일 기준)."""
    if len(df) < 26:
        return False
    ema10 = calc_ema(df, 10)
    ema21 = calc_ema(df, 21)
    if float(ema10.iloc[-1]) <= float(ema21.iloc[-1]):
        return False
    slope10 = float(ema10.iloc[-1]) - float(ema10.iloc[-2])
    slope21 = float(ema21.iloc[-1]) - float(ema21.iloc[-2])
    return slope10 > 0 and slope21 > 0

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


# 현재가는 접속·새로고침 때 갱신 — 1분 캐시를 모든 세션이 공유해 yfinance 호출은 분당 지수당 1회 이하
@st.cache_data(ttl=60, show_spinner=False)
def _live_quote(name: str):
    return live_index.fetch_live_quote(name)


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

            adr   = calc_adr(df)
            phase = get_phase_label(df, adr)
            icon  = PHASE_COLORS.get(phase, '⚪')
            desc  = PHASE_DESC.get(phase, '')

            # Normal일 때 찐반등 여부로 세분화 — DAY2~7은 get_phase_label이 이미 처리
            if phase == 'Normal':
                status = get_market_status(df)
                if status.get('jjin_date'):
                    icon = '🟢'
                    desc = f"정상 (찐반등 확인 {status['jjin_date'].date()})"
                elif status.get('correction_start'):
                    icon = '⚫'
                    desc = '정상 (찐반등 미확인)'

            # 현재가·등락률만 실시간 — 국면·ADR·PP는 위에서 배치 확정 일봉으로 계산했다
            disp = live_index.display_values(df, _live_quote(name))

            st.metric(
                label=f'{icon} {name}',
                value=f'{disp.value:,.2f}',
                delta=f'{disp.delta_pct:+.2f}%',
            )
            pp = '✅ 10EMA (PP) 거래 가능' if _pp_ok(df) else '❌ 10EMA (PP) 거래 불가'
            st.caption(f'ADR {adr:.2f}%  |  **{phase}** {desc}  |  {pp}')
            if disp.live:
                delay = live_index.DELAY_NOTE.get(name, '')
                src = f'yfinance {delay}, 잠정' if delay else 'yfinance, 잠정'
                st.caption(f'🔴 장중 시세 {disp.fetched_at:%H:%M} 조회 ({src}) · '
                           f'등락률은 {df.index[-1]:%m-%d} 확정 종가 대비 · 국면은 장마감 확정 기준')
