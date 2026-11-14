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


# ── M11: 자세히 보기 → 섹터별 종목도 선택한 기준일로 ──────────────────

def _is_res_asof(node) -> bool:
    """`res['asof']`인가."""
    return (isinstance(node, ast.Subscript) and _name(node.value) == 'res'
            and isinstance(node.slice, ast.Constant) and node.slice.value == 'asof')


def _arg(call: ast.Call, pos: int, key: str):
    return call.args[pos] if len(call.args) > pos else _kw(call, key)


def test_lead_scores_computes_at_given_asof():
    """lead_scores가 늘 최신일(opts[0])로만 계산하면 과거 기준일 화면의 '자세히 보기' 점수가 최신일 값이 된다."""
    fn = _function(SECTOR, 'lead_scores')
    assert 'asof' in [a.arg for a in fn.args.args]
    calls = _calls(fn, '_compute')
    assert calls and all(any(_name(n) == 'asof' for n in ast.walk(c.args[1])) for c in calls)


def test_drilldown_rows_and_scores_use_selected_asof():
    """섹터별 종목 표의 종가·등락(_build_rows)과 주도주 점수(lead_scores)가 위쪽 표와 같은 기준일이어야 한다."""
    fn = _function(SECTOR, '_drilldown')
    rows = _calls(fn, '_build_rows')
    assert rows and all(_is_res_asof(_arg(c, 5, 'as_of_date_str')) for c in rows)
    lead = _calls(fn, 'lead_scores')
    assert lead and all(_is_res_asof(_arg(c, 3, 'asof')) for c in lead)


# ── M12: 10EMA '🔥 섹터' 배지는 종목 테마를 분류 기록에서 직접 찾는다 ──────

def test_hot_history_returns_only_hot_and_labels():
    """hot_history의 종목→테마 지도는 주도섹터 유니버스(US 210)만 담는다 — 10EMA 탭에 넘기면
    유니버스 밖 종목은 소속 테마가 🔥여도 배지가 영영 안 붙는다. 계약을 (🔥 섹터, 라벨)로 좁힌다."""
    fn = _function(SECTOR, 'hot_history')
    rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert rets and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in rets)


def test_10ema_badge_looks_up_theme_by_classification():
    fn = _function('ui/watchlist_10ema.py', 'render_10ema_tab')
    assert _calls(fn, 'primary_map'), '10EMA 탭이 cls.primary_map으로 종목 테마를 찾아야 한다'
    unpack = [a for a in ast.walk(fn) if isinstance(a, ast.Assign) and _calls(a.value, 'hot_history')]
    assert unpack and all(isinstance(a.targets[0], ast.Tuple) and len(a.targets[0].elts) == 2 for a in unpack)


# ── M13: 분류 저장 뒤엔 분류를 쓰는 캐시만 비운다 ─────────────────────

def _clear_targets(node) -> list[str]:
    """`x.clear()`의 대상 — `st.cache_data.clear()`는 'cache_data'로 나온다."""
    return [_name(c.func.value) for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'clear']


def test_classification_save_does_not_clear_every_cache():
    """st.cache_data.clear()는 앱 전체·전 사용자 캐시(10EMA 3탭 수 분짜리 스캔 포함)를 날린다."""
    fn = _function(SECTOR, '_classification_editor')
    assert 'cache_data' not in _clear_targets(fn)


def test_classification_save_refreshes_trend_rows():
    """추세추종 행(_build_rows)의 섹터·테마 열은 분류를 읽지만 캐시 키에 분류가 없다 — 저장 뒤 비워야 바로 보인다."""
    fn = _function(SECTOR, '_classification_editor')
    assert '_build_rows' in _clear_targets(fn)


def test_sector_compute_key_tracks_classification_file():
    """_compute는 분류 파일 mtime이 캐시 키(_data_key)에 있어 저장만으로 다시 계산된다 — 전역 clear가 필요 없는 근거."""
    fn = _function(SECTOR, '_data_key')
    assert _calls(fn, '_store_path')
