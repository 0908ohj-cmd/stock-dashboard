"""추세추종 탭(ui/watchlist.py)의 순수 로직 — Streamlit을 import하지 않아 pytest로 검증한다.

이 모듈에 streamlit import를 넣지 말 것 (tests/test_watchlist_logic.py가 streamlit 없이 돈다).
"""
from __future__ import annotations

import json
import re


# ── 차트 선택 (#44 M16) ───────────────────────────────────
# 선택은 행 번호가 아니라 티커로 저장하고, 위치는 매번 지금 표에서 다시 찾는다.
# 행 번호를 저장하면 종목 수가 줄 때 IndexError(그 뒤 탭·내보내기까지 멈춤), 정렬이 바뀌면 다른 종목이 된다.

def chart_position(tickers: list[str], selected: str | None) -> int | None:
    """selected 티커의 지금 표 위치. 선택이 없거나 표에서 빠졌으면 None."""
    if selected is None:
        return None
    try:
        return tickers.index(selected)
    except ValueError:
        return None


def chart_step(tickers: list[str], selected: str | None, delta: int) -> str | None:
    """◀/▶ 이동 — delta칸 옆 종목(끝에서 처음으로 순환). 선택이 표에 없으면 None."""
    pos = chart_position(tickers, selected)
    if pos is None:
        return None
    return tickers[(pos + delta) % len(tickers)]


def nav_button_keys(market: str) -> tuple[str, str]:
    """(◀ 키, ▶ 키) — st.button 키와 스페이스바 스크립트가 같은 값을 쓴다."""
    return f'prev_{market}', f'next_{market}'


def _key_class(key: str) -> str:
    """Streamlit이 키가 있는 요소 컨테이너에 붙이는 CSS 클래스 (st-key-<키>, 영숫자·_·- 밖은 '-')."""
    return 'st-key-' + re.sub(r'[^a-zA-Z0-9_-]', '-', key.strip())


def space_nav_script(market: str) -> str:
    """스페이스바 = ▶, Shift+스페이스바 = ◀ — 이 탭의 버튼만 누른다.

    예전엔 문서 전체의 첫 '▶'를 눌러 다른 탭 차트가 넘어갈 수 있었고, 전역 핸들러 하나를
    마지막에 그린 탭이 덮어썼다. 탭마다 자기 핸들러를 따로 등록하고, 자기 버튼이 보일 때(활성 탭)만
    누른다. 숨은 탭이면 키 입력을 막지 않고 넘긴다.
    """
    prev_key, next_key = nav_button_keys(market)
    m = json.dumps(market)
    prev_sel = json.dumps(f'.{_key_class(prev_key)} button')
    next_sel = json.dumps(f'.{_key_class(next_key)} button')
    return f"""
<script>
(function() {{
    var p = window.parent;
    var market = {m};
    // 예전 전역 핸들러(화면의 첫 ▶ 버튼을 누름)가 열린 페이지에 남아 있으면 떼어 낸다
    if (p.__spaceNavHandler) {{
        p.document.removeEventListener('keydown', p.__spaceNavHandler);
        p.__spaceNavHandler = null;
        p.__spaceNavRegistered = false;
    }}
    p.__spaceNav = p.__spaceNav || {{}};
    if (p.__spaceNav[market]) {{
        p.document.removeEventListener('keydown', p.__spaceNav[market]);
    }}
    function visible(el) {{
        return el.offsetParent !== null && el.getClientRects().length > 0;
    }}
    p.__spaceNav[market] = function(e) {{
        if (e.code !== 'Space' && e.key !== ' ') return;
        var tag = (p.document.activeElement || {{}}).tagName || '';
        if (/^(INPUT|TEXTAREA|SELECT)$/i.test(tag)) return;
        var btn = p.document.querySelector(e.shiftKey ? {prev_sel} : {next_sel});
        if (!btn || !visible(btn)) return;
        e.preventDefault();
        btn.click();
    }};
    p.document.addEventListener('keydown', p.__spaceNav[market]);
}})();
</script>
"""


# ── 주도주 점수 빈칸 사유 (#44 M20) ───────────────────────
# 주도주 점수·하락/상승국면 RS/ADR은 lead_scores(주도섹터 탭 _compute) 결과만 쓴다 — 세 화면 일관성.
# 계산 대상은 주도섹터 유니버스(US 210종목, KR 와치리스트+10EMA) 안의 분류 완료·데이터 정상(FULL) 종목뿐이라
# 그 밖 종목은 빈칸이다. 다른 계산(_build_rows의 RS/ADR)으로 채우면 같은 열에 정의가 다른 값이 섞이므로
# 값은 비워 두고 사유만 보여 준다.
GAP_OUTSIDE = '대상 밖'          # 주도섹터 유니버스 밖 (US는 기술·혁신 210종목 밖)
GAP_UNCLASSIFIED = '미분류'      # 테마 분류 기록 없음 또는 UNCLASSIFIED — 섹터 집계·점수에서 빠진다
GAP_DATA = '데이터 부족'          # 대상·분류는 됐지만 점수 없음 (기준일 종가 없음·이력 부족·이상 데이터 등)
GAP_NO_CALC = '산출 불가'         # 주도섹터 계산이 점수를 하나도 내지 못함
_GAP_ORDER = (GAP_OUTSIDE, GAP_UNCLASSIFIED, GAP_DATA, GAP_NO_CALC)
_UNCLASSIFIED = 'UNCLASSIFIED'   # data.classification.UNCLASSIFIED와 같은 값


def lead_gap_reasons(tickers: list[str], scored: set, universe: set | None,
                     theme_of: dict) -> dict[str, str]:
    """{티커: 빈칸 사유} — 점수가 있는 종목은 빠진다.

    scored: lead_scores 결과의 티커. universe: 주도섹터 계산 유니버스(못 읽었으면 None — '대상 밖'으로
    단정하지 않는다). theme_of: {티커: 주력 테마 코드}(classification.primary_map).
    """
    out = {}
    for t in tickers:
        if t in scored:
            continue
        if universe is not None and t not in universe:
            out[t] = GAP_OUTSIDE
        elif theme_of.get(t) in (None, _UNCLASSIFIED):
            out[t] = GAP_UNCLASSIFIED
        elif not scored:
            out[t] = GAP_NO_CALC
        else:
            out[t] = GAP_DATA
    return out


def lead_gap_caption(reasons: dict[str, str], total: int) -> str | None:
    """표 위 캡션 — 빈칸 종목 수와 사유별 개수. 빈칸이 없으면 None."""
    if not reasons:
        return None
    counts = {g: 0 for g in _GAP_ORDER}
    for g in reasons.values():
        counts[g] = counts.get(g, 0) + 1
    parts = ' · '.join(f'{g} {n}' for g, n in counts.items() if n)
    return (f'ℹ️ 주도주 점수·RS/ADR·200일선 빈칸 {len(reasons)}종목 ({total}종목 중) — {parts}. '
            '점수는 주도섹터 탭 계산 대상(분류 완료·데이터 정상 종목, 나스닥은 기술·혁신 210종목)에만 나오며, '
            '빈칸에는 사유를 표시합니다.')
