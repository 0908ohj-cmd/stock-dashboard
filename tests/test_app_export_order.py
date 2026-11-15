"""TradingView 내보내기 버튼 렌더 위치 정적 점검 (#43 L17).

테스트 환경엔 Streamlit이 없어 app.py를 돌려볼 수 없다. 소스를 AST로 읽어 호출 순서를 본다.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding='utf-8'))


def _calls(tree: ast.AST, name: str) -> list:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', None)
            if fname == name:
                out.append(node)
    return out


def _kw(call: ast.Call, arg: str):
    for k in call.keywords:
        if k.arg == arg and isinstance(k.value, ast.Constant):
            return k.value.value
    return None


def test_final_export_render_runs_before_10ema_scans():
    """최종 내보내기 렌더는 후보를 등록하는 탭(추세추종·주도섹터) 뒤, 10EMA 탭 앞에 있어야 한다.

    10EMA 섹션은 내보내기에서 빠졌는데 최종 렌더가 10EMA 3탭 뒤에 있으면, 캐시가 빈
    첫 로드에 스캔(수 분)이 끝날 때까지 버튼이 '⏳'로 잠기고 10EMA 탭이 예외로 죽으면
    끝까지 잠긴다.
    """
    tree = _tree('app.py')
    final = [c.lineno for c in _calls(tree, 'render_export_button') if _kw(c, 'key') == 'final']
    registering = [c.lineno for name in ('render_watchlist_tab', 'render_sector_leadership_tab')
                   for c in _calls(tree, name)]
    ema10 = [c.lineno for c in _calls(tree, 'render_10ema_tab')]
    assert len(final) == 1 and registering and ema10              # 전제: 세 종류 호출이 모두 있다

    assert max(registering) < final[0] < min(ema10)


def test_10ema_tab_registers_only_empty_export_section():
    """최종 렌더를 10EMA 탭 앞에 둘 수 있는 전제 — 10EMA 탭은 빈 목록으로 비우기만 한다.

    10EMA 셋업을 다시 내보내려면 이 테스트를 바꾸고 app.py의 최종 렌더를 10EMA 탭 뒤로
    되돌려야 한다. 안 그러면 버튼이 직전 실행의 10EMA 목록을 싣는다.
    """
    calls = _calls(_tree('ui/watchlist_10ema.py'), 'register_ema10')
    assert calls                                                   # 선비움 계약은 유지

    for c in calls:
        tickers = c.args[1]
        assert isinstance(tickers, ast.List) and not tickers.elts
