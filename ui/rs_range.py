"""'⚙️ RS 구간 설정' 위젯 — 추세추종 탭과 주도섹터 탭이 같은 설정을 쓴다 (#40 M5).

값은 위젯 키가 아니라 공유 키 `{market}_rs_peak` / `{market}_rs_jjin`(session_state)에 둔다.
두 탭은 같은 실행에서 함께 그려지므로 위젯 키는 탭마다 달라야 한다(같은 key 위젯 두 개 = 중복 키 오류).
그리기 전에 공유 값 → 위젯, 사용자가 바꾸면 on_change로 위젯 → 공유 값. 그래서 한쪽에서 지정하면
다른 탭도 같은 날짜를 보이고, 같은 값으로 주도주 점수(sector_leadership._compute)를 부른다.
공유 키는 위젯이 아니어서 한 탭이 조기 return해 위젯을 못 그려도 값이 지워지지 않는다.
"""
import pandas as pd
import streamlit as st

_FIELDS = (
    ('peak', '기산점', '기산점 수동 설정', '없음 (조정 미감지)',
     '비워두면 지수가 21EMA를 이탈하기 전 고점을 씁니다. 하락국면 RS/ADR·덜 빠짐·저점 선행이 이 날짜부터 다시 계산됩니다.'),
    ('jjin', '찐반등일', '찐반등일 수동 설정', '없음 (미감지)',
     '비워두면 자동 감지한 찐반등일을 씁니다. 지정하면 찐반등 이후 화면으로 바뀌고 '
     '상승국면 RS/ADR이 이 날짜부터 계산됩니다.'),
)


def shared_key(market: str, kind: str) -> str:
    """두 탭이 함께 읽고 쓰는 값의 키 — kind: 'peak' | 'jjin'."""
    return f'{market}_rs_{kind}'


def _store(widget_key: str, key: str):
    st.session_state[key] = st.session_state.get(widget_key)


def rs_range_inputs(market: str, tab: str, auto_peak=None, auto_jjin=None, max_date=None,
                    note: str = '') -> tuple:
    """'⚙️ RS 구간 설정' 접기를 그리고 (기산점, 찐반등일) 수동 값을 돌려준다 — 없으면 None.
    tab: 위젯 키 구분용('trend' | 'sector'). max_date: 고를 수 있는 마지막 날 (None이면 제한 없음).
    돌려준 값은 rs_correction.clean_manual_rs_range로 정리해서 쓴다."""
    autos = {'peak': auto_peak, 'jjin': auto_jjin}
    with st.expander('⚙️ RS 구간 설정', expanded=False):
        cols = st.columns(2)
        for col, (kind, name, label, none_txt, help_) in zip(cols, _FIELDS):
            key = shared_key(market, kind)
            widget_key = f'{key}__{tab}'
            st.session_state[widget_key] = st.session_state.get(key)      # 다른 탭에서 바꾼 값도 보이게
            auto = autos[kind]
            col.caption(f'자동 {name}: {pd.Timestamp(auto):%Y-%m-%d}' if auto is not None else f'자동 {name}: {none_txt}')
            col.date_input(label, value=None, max_value=max_date, key=widget_key, help=help_,
                           on_change=_store, args=(widget_key, key))
        st.caption('추세추종 탭과 주도섹터 탭이 같은 설정을 씁니다 — 한쪽에서 바꾸면 다른 탭도 같은 구간으로 계산합니다. '
                   '휴장일을 고르면 직전 거래일로 맞춥니다.' + (f' {note}' if note else ''))
    return st.session_state.get(shared_key(market, 'peak')), st.session_state.get(shared_key(market, 'jjin'))


def show_warnings(warnings: list):
    for w in warnings:
        if w:
            st.warning(f'**RS 구간 설정** — {w}', icon='⚠️')


def date_str(d) -> str:
    """점수 캐시 키용 'YYYY-MM-DD' — 두 탭이 같은 문자열을 넘기게. 없으면 ''."""
    return '' if d is None else f'{pd.Timestamp(d):%Y-%m-%d}'
