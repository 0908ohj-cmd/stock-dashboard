import streamlit as st
from data import store
from strategy.indicators import calc_adr, calc_ema
from strategy.market_status import get_market_status
from strategy.trading_days import trading_days_after


def _pp_ok(df) -> bool:
    """PP 거래 가능 조건: 지수 10EMA > 21EMA, 두 EMA 모두 기울기 양수 (5일 기준)."""
    if len(df) < 26:
        return False
    ema10 = calc_ema(df, 10)
    ema21 = calc_ema(df, 21)
    if float(ema10.iloc[-1]) <= float(ema21.iloc[-1]):
        return False
    slope10 = float(ema10.iloc[-1]) - float(ema10.iloc[-6])
    slope21 = float(ema21.iloc[-1]) - float(ema21.iloc[-6])
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
            status = get_market_status(df)
            state  = status['state']

            # get_market_status state를 1차 판단 기준으로 사용 — 와치리스트 배너와 동일한 로직
            if state == 'normal':
                if status.get('jjin_date'):
                    icon  = '🟢'
                    phase = 'Normal'
                    desc  = f"정상 (찐반등 확인 {status['jjin_date'].date()})"
                else:
                    icon  = '⚫'
                    phase = 'Normal'
                    desc  = '정상 (찐반등 미확인)'
            elif state == 'correction':
                icon  = '🔴'
                phase = 'DAY1'
                desc  = PHASE_DESC['DAY1']
            elif state == 'early_signal':
                days_since = trading_days_after(df, status['jjin_date'])
                phase = f'DAY{min(days_since + 2, 7)}'
                icon  = PHASE_COLORS.get(phase, '⚪')
                desc  = PHASE_DESC.get(phase, '')
            else:
                icon  = '⚪'
                phase = 'Normal'
                desc  = ''

            last = float(df['Close'].iloc[-1])
            prev = float(df['Close'].iloc[-2])
            delta_pct = (last - prev) / prev * 100

            st.metric(
                label=f'{icon} {name}',
                value=f'{last:,.2f}',
                delta=f'{delta_pct:+.2f}%',
            )
            pp = '✅ 10EMA (PP) 거래 가능' if _pp_ok(df) else '❌ 10EMA (PP) 거래 불가'
            st.caption(f'ADR {adr:.2f}%  |  **{phase}** {desc}  |  {pp}')
