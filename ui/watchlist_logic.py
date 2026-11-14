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
