"""st.cache_data 캐시 키·무효화 정적 점검 (#32).

테스트 환경(시스템 python)엔 Streamlit이 없어 캐시를 직접 돌려볼 수 없다. 그래서 소스를 AST로
읽어, 스냅샷이 바뀌어도 캐시가 안 깨지던 세 가지 실수가 다시 들어오지 않게 막는다.
  - 밑줄 인자: st.cache_data는 `_`로 시작하는 인자를 해시하지 않는다 (20d3cf0, #32 b)
  - 스냅샷 시각을 안 넘기는 호출: 10EMA 표는 키가 (티커, 시장, 스키마)뿐이라 최대 1시간 낡았다 (#32 a)
  - 재수집·동기화 뒤 비우는 목록 누락: _clear_analysis_caches에 10EMA가 없었다 (#32 a)
실제 재계산 횟수는 AppTest 하네스로 따로 확인했다 (PR 설명 참고).
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ['app.py'] + sorted(
    str(p.relative_to(ROOT)) for d in ('ui', 'data') for p in (ROOT / d).glob('*.py'))


def _tree(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding='utf-8'))


def _name(node) -> str | None:
    """`f` / `mod.f` → 'f'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_cache_data(dec) -> bool:
    return _name(dec.func if isinstance(dec, ast.Call) else dec) == 'cache_data'


def _functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _params(fn) -> list[str]:
    a = fn.args
    return [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]


def _calls(node, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _name(n.func) == name]


def _cleared(node) -> set[str]:
    """`x.clear()` 호출의 대상 이름들."""
    return {_name(c.func.value) for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == 'clear'}


def _function(rel: str, name: str):
    return next(f for f in _functions(_tree(rel)) if f.name == name)


# ── 밑줄 인자 ─────────────────────────────────────────────

def test_cache_data_params_are_all_hashed():
    """밑줄 인자는 캐시 키에서 빠진다 — 해시할 수 없는 객체를 넘겨야 할 때만 예외로 허용할 것."""
    bad = [f'{rel}:{fn.name}({p})'
           for rel in SOURCES for fn in _functions(_tree(rel))
           if any(_is_cache_data(d) for d in fn.decorator_list)
           for p in _params(fn) if p.startswith('_')]
    assert bad == []


# ── 스냅샷 시각을 캐시 키로 넘기는가 ──────────────────────

def _snapshot_ts_args(call: ast.Call, scope) -> int:
    """호출 인자 중 스냅샷 시각(_snapshot_ts(...) 또는 그 결과를 담은 변수)의 개수."""
    ts_vars = {t.id for a in ast.walk(scope) if isinstance(a, ast.Assign) and _calls(a.value, '_snapshot_ts')
               for t in a.targets if isinstance(t, ast.Name)}
    args = list(call.args) + [k.value for k in call.keywords]
    return sum(1 for a in args
               if _calls(a, '_snapshot_ts') or (isinstance(a, ast.Name) and a.id in ts_vars))


def _builder_calls(builder: str):
    for rel in SOURCES:
        for fn in _functions(_tree(rel)):
            if fn.name == builder:
                continue
            for call in _calls(fn, builder):
                yield f'{rel}:{fn.name}', call, fn


def test_trend_rows_cache_key_has_stock_and_index_snapshot_ts():
    """_build_rows는 종목 스냅샷과 지수 스냅샷을 모두 읽는다 — 둘 다 캐시 키에 들어가야 한다."""
    assert {'snap_ts', 'idx_ts'} <= set(_params(_function('ui/watchlist.py', '_build_rows')))
    calls = list(_builder_calls('_build_rows'))
    assert calls, '_build_rows 호출부를 찾지 못했다'
    for where, call, scope in calls:
        assert _snapshot_ts_args(call, scope) == 2, f'{where}: 종목·지수 스냅샷 시각을 모두 넘길 것'


def test_trend_rows_reads_index_with_its_snapshot_ts():
    """_build_rows 안의 지수 조회도 같은 ts로 — 빠지면 지수만 옛 캐시(ts='')에 머문다."""
    fn = _function('ui/watchlist.py', '_build_rows')
    for call in _calls(fn, '_fetch_index_cached'):
        assert len(call.args) + len(call.keywords) == 2


def test_10ema_rows_cache_key_has_snapshot_ts():
    assert 'snap_ts' in _params(_function('ui/watchlist_10ema.py', '_build_10ema_rows'))
    calls = list(_builder_calls('_build_10ema_rows'))
    assert calls, '_build_10ema_rows 호출부를 찾지 못했다'
    for where, call, scope in calls:
        assert _snapshot_ts_args(call, scope) == 1, f'{where}: 종목 스냅샷 시각을 넘길 것'


# ── 재수집·동기화 뒤 무효화 ───────────────────────────────

def test_clear_analysis_caches_covers_every_snapshot_cache():
    cleared = _cleared(_function('app.py', '_clear_analysis_caches'))
    assert {
        '_snapshot_ts',                     # 먼저 비워야 아래 키가 곧바로 새 fetched_at을 본다 (TTL 120초)
        '_build_rows', '_fetch_index_cached', '_get_market_status_cached', '_load_index',
        '_build_10ema_rows',
        'get_kr_10ema_universe', 'get_us_10ema_universe',   # 배치가 스냅샷과 같은 커밋으로 갱신한다
    } <= cleared


def test_every_refetch_refreshes_snapshot_ts():
    """탭의 [재스캔]이 자기 표만 비우면 다른 탭은 _snapshot_ts TTL(120초)이 지날 때까지 옛 표를 보인다."""
    bad = []
    for rel in SOURCES:
        tree = _tree(rel)
        scopes = _functions(tree) + ([tree] if rel == 'app.py' else [])
        for scope in scopes:
            if not (_calls(scope, 'refetch_market') or _calls(scope, 'refetch_indices')):
                continue
            if scope is not tree and getattr(scope, 'name', '') in ('refetch_market', 'refetch_indices'):
                continue
            if '_snapshot_ts' not in _cleared(scope) and not _calls(scope, '_clear_analysis_caches'):
                bad.append(f"{rel}:{getattr(scope, 'name', '<module>')}")
    assert bad == []
