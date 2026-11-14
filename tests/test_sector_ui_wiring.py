"""주도섹터 탭·10EMA 배지 배선 정적 점검 (#42).

테스트 환경(시스템 python)엔 Streamlit이 없어 ui 모듈을 import할 수 없다. 순수 로직은
strategy/sector_view.py·strategy/tv_export.py에서 따로 검증하고, 여기서는 그 로직이 화면에
제대로 꽂혔는지를 소스 AST로 확인한다 (tests/test_cache_keys.py와 같은 방식).
실제 동작은 AppTest 하네스로 따로 확인했다 (PR 설명 참고).
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECTOR = 'ui/sector_leadership.py'


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding='utf-8'))


def _name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _function(rel: str, name: str):
    return next(n for n in ast.walk(_tree(rel)) if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(node, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _name(n.func) == name]


def _kw(call: ast.Call, key: str):
    return next((k.value for k in call.keywords if k.arg == key), None)


# ── M10: 기준일은 직접 고른 날만 기억, 내보내기 제목에 기준일 ─────────

def test_sector_tab_resolves_asof_from_user_pick_not_leftover_session_value():
    """세션에 남은 값이 선택지에 있으면 유지하던 방식은 새 배치를 못 따라간다 — resolve_asof로 정할 것."""
    fn = _function(SECTOR, 'render_sector_leadership_tab')
    assert _calls(fn, 'resolve_asof'), 'render_sector_leadership_tab이 resolve_asof로 기준일을 정해야 한다'


def test_asof_selectbox_records_user_pick():
    """사용자가 기준일 상자를 바꿨을 때만 그 날을 기억한다 — on_change 없이는 '직접 고름'을 알 수 없다."""
    fn = _function(SECTOR, 'render_sector_leadership_tab')
    boxes = [c for c in _calls(fn, 'selectbox') if _name(_kw(c, 'key')) == 'key_asof']
    assert boxes, '기준일 selectbox를 찾지 못했다'
    assert all(_kw(c, 'on_change') is not None for c in boxes)


def test_lead_and_hot_sections_are_registered_with_asof():
    """빈 목록(먼저 비우기)이 아닌 등록은 기준일을 함께 넘겨 섹션 제목에 붙게 한다."""
    bad = []
    for name in ('register_lead', 'register_hot'):
        for c in _calls(_tree(SECTOR), name):
            tickers = c.args[1] if len(c.args) > 1 else None
            empty = isinstance(tickers, ast.List) and not tickers.elts
            if not empty and len(c.args) + len(c.keywords) < 3:
                bad.append(f'{name} (line {c.lineno})')
    assert bad == []
