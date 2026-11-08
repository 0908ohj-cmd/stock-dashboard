"""주도섹터 탭 — 나스닥(210종목 고정 유니버스)·코스피·코스닥 섹터 강도 (마스터 명세 9장).

시세는 배치 스냅샷(data/ohlcv/*.json)만 읽는다. 스냅샷에 없는 종목은 MISSING으로
표시하고 실시간으로 받지 않는다 (명세 3장: 화면에서 일괄 다운로드 금지).
"""
import base64
import html
import json
import pathlib
from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from data import classification as cls
from data import store
from strategy import next_leaders as nl
from strategy import sector_strength as ss

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SAVED = _ROOT / 'data' / 'saved'
_US_UNIVERSE = _ROOT / 'data' / 'universe' / 'us_innovation_210.json'
_KR_NAMES = _ROOT / 'data' / 'kr_names.json'
_GITHUB_REPO = '0908ohj-cmd/stock-dashboard'


@dataclass(frozen=True)
class Market:
    key: str                 # 스냅샷·분류 시장 키
    title: str
    bench: tuple             # 벤치마크 (US 2개, KR은 자기 시장 지수 1개)
    bench_snapshot: str      # 벤치마크 일봉이 있는 스냅샷
    ticker_files: tuple      # 유니버스(US는 고정 파일 사용)
    liquidity_shares: int | None
    liquidity_value: float | None
    value_unit: str          # 거래대금 표시 단위
    value_div: float
    phase_index: str         # 국면 판정 지수 (추세추종 탭과 같은 지수)


MARKETS = {
    'US': Market('US', '나스닥 기술·혁신 성장주 210종목', ('QQQ', 'IWM'), 'US', (),
                 1_000_000, None, '$M', 1e6, 'NASDAQ'),
    'KR_KOSPI': Market('KR_KOSPI', '코스피 와치리스트', ('KOSPI',), 'indices',
                       ('kospi.tickers', 'kospi_10ema.tickers'), None, 5e9, '억원', 1e8, 'KOSPI'),
    'KR_KOSDAQ': Market('KR_KOSDAQ', '코스닥 와치리스트', ('KOSDAQ',), 'indices',
                        ('kosdaq.tickers', 'kosdaq_10ema.tickers'), None, 5e9, '억원', 1e8, 'KOSDAQ'),
}

GRADE_LABEL = {
    'A_CONFIRMED': 'A 확인된 주도',
    'B_EMERGING': 'B 차기 전환 후보',
    'C_WATCH': 'C 관찰',
    'UNVERIFIED': '미검증',
}
TYPE_LABEL = {
    'LEADER_TREND': '기존 강세 유지형',
    'EMERGING': '초기 전환형',
    'REBOUND': '낙폭 반등형',
    'OTHER': '해당 없음',
}
STATUS_LABEL = {
    'FULL': '정상', 'MISSING': '데이터 없음', 'STALE': '기준일 종가 없음',
    'SHORT_HISTORY': '이력 부족', 'INVALID_DATA': '이상 데이터',
}


# ── 데이터 ───────────────────────────────────────────────

def _universe(m: Market) -> list:
    """[(ticker, legacySector or '')]. US는 고정 210종목, KR은 현재 와치리스트 + 10EMA 목록."""
    if m.key == 'US':
        uni = json.loads(_US_UNIVERSE.read_text(encoding='utf-8'))
        return [(s['ticker'], s['legacySector']) for s in uni['stocks']]
    out = []
    for name in m.ticker_files:
        p = _SAVED / name
        if p.exists():
            out += [x.strip() for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
    return [(t, '') for t in dict.fromkeys(out) if t not in m.bench]


def _names(m: Market) -> dict:
    if m.key == 'US':
        return {}
    try:
        return json.loads(_KR_NAMES.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _mtime(path: pathlib.Path) -> int:
    return path.stat().st_mtime_ns if path.exists() else 0


def _data_key(m: Market) -> tuple:
    """스냅샷·분류·유니버스 파일 mtime — 바뀌면 다시 계산한다."""
    paths = [store._snapshot_path(m.key), store._snapshot_path(m.bench_snapshot),
             cls._store_path(m.key), _US_UNIVERSE] + [_SAVED / f for f in m.ticker_files]
    return tuple(_mtime(p) for p in paths)


@st.cache_data(ttl=1800, show_spinner=False)
def _compute(market_key: str, asof: str, data_key: tuple, peak: str = '', jjin: str = '') -> dict:
    m = MARKETS[market_key]
    tickers = [t for t, _ in _universe(m)]
    data = store.load_snapshot(m.key).get('data', {})
    bars = {t: store._records_to_df(data[t]) for t in tickers if t in data}
    bdata = store.load_snapshot(m.bench_snapshot).get('data', {})
    for b in m.bench:
        if b in bdata:
            bars[b] = store._records_to_df(bdata[b])
    tax = cls.load_taxonomy(m.key)
    main = {t['code'] for t in tax['themes'] if t['main']}
    theme_of = cls.primary_map(m.key, tickers)
    kw = dict(bench_names=m.bench, liquidity_shares=m.liquidity_shares, liquidity_value=m.liquidity_value)
    rep = ss.run(bars, tickers, theme_of, main, asof, tax['version'], **kw)
    # 변화 감지 — 1주(5거래일)·2주 전 같은 구성으로 다시 계산해 섹터 추이를 붙인다
    bench_dates = [d.strftime('%Y-%m-%d') for d in rep.bench.dates]
    pos = bench_dates.index(rep.asof)
    earlier = []
    for back in (5, 10):
        if pos - back >= 0:
            try:
                earlier.append(ss.run(bars, tickers, theme_of, main, bench_dates[pos - back],
                                      tax['version'], **kw).sectors)
            except ValueError:
                break
    ss.attach_trend(rep.sectors, earlier)
    idx_rec = store.load_snapshot('indices').get('data', {}).get(m.phase_index)
    phase, picks, ranked, thin = None, [], [], []
    if idx_rec:
        idx_df = store._records_to_df(idx_rec)
        phase = nl.detect_phase(idx_df, asof, peak or None, jjin or None)
        picks = nl.score_stocks(rep.stocks, bars, idx_df, theme_of, main, phase, asof)
        ranked, thin = nl.rank_sectors(picks)
    return {
        'asof': rep.asof,
        'bench': {'names': list(rep.bench.names), 'ret5': rep.bench.ret5, 'ret21': rep.bench.ret21,
                  'blend5': rep.bench.blend5, 'blend21': rep.bench.blend21},
        'stocks': rep.stocks, 'sectors': rep.sectors, 'leaders': rep.leaders,
        'coverage': rep.coverage, 'taxonomyVersion': tax['version'],
        'phase': phase, 'picks': picks, 'nextSectors': ranked, 'thinSectors': thin,
    }


def _asof_options(m: Market) -> list:
    data = store.load_snapshot(m.bench_snapshot).get('data', {})
    recs = [data.get(b) for b in m.bench]
    if not all(recs):
        return []
    common = set(recs[0]['dates'])
    for r in recs[1:]:
        common &= set(r['dates'])
    return sorted(common, reverse=True)[:60]


def _labels(m: Market) -> dict:
    return {t['code']: t['label'] for t in cls.load_taxonomy(m.key)['themes']}


def _pct(v, digits=0):
    return '—' if v is None else f'{100 * v:.{digits}f}%'


def _num(v, digits=2, signed=True):
    if v is None:
        return '—'
    return f'{v:+.{digits}f}' if signed else f'{v:.{digits}f}'


# ── 핵심 화면: 차기 주도섹터 · 차기 주도주 ─────────────────
# 판정 기준은 뉴스나 하루 상승폭이 아니라 1주·1개월 상대강도와 10EMA 위 유지 비율이다.

_CSS = """<style>
.sl-wrap{overflow-x:auto;margin:4px 0 18px}
.sl-t{width:100%;border-collapse:collapse;font-size:.88rem;font-variant-numeric:tabular-nums}
.sl-t th{font-size:.74rem;font-weight:600;letter-spacing:.02em;opacity:.6;text-align:left;
  padding:6px 10px;border-bottom:1px solid rgba(128,128,128,.25);white-space:nowrap}
.sl-t td{padding:9px 10px;border-bottom:1px solid rgba(128,128,128,.12);vertical-align:middle;white-space:nowrap}
.sl-t tr.dim td{opacity:.45}
.sl-name{font-weight:600}
.sl-sub{font-size:.74rem;opacity:.55;margin-left:4px}
.sl-badge{display:inline-block;font-size:.72rem;font-weight:700;padding:2px 8px;border-radius:999px}
.sl-A{background:#16a34a;color:#fff}
.sl-B{background:#f59e0b;color:#1f1300}
.sl-C{border:1px solid rgba(128,128,128,.5);opacity:.8}
.sl-U{border:1px dashed rgba(128,128,128,.5);opacity:.6}
.sl-bar{position:relative;height:6px;width:110px;background:rgba(128,128,128,.15);border-radius:3px;margin-top:3px}
.sl-bar i{position:absolute;top:0;height:6px;border-radius:3px}
.sl-bar b{position:absolute;top:-3px;width:1px;height:12px;background:rgba(128,128,128,.7)}
.sl-pos{color:#16a34a}.sl-neg{color:#dc2626}
.sl-need{font-size:.76rem;opacity:.75;white-space:normal;min-width:160px}
.sl-note{font-size:.8rem;opacity:.7;margin:-4px 0 10px}
.sl-mkt{display:flex;flex-wrap:wrap;gap:12px;margin:4px 0 16px}
.sl-stat{border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:8px 16px 10px;min-width:120px;
  background:rgba(128,128,128,.04)}
.sl-stat.neg{background:rgba(220,38,38,.06);border-color:rgba(220,38,38,.3)}
.sl-stat.pos{background:rgba(22,163,74,.06);border-color:rgba(22,163,74,.3)}
.sl-stat .k{font-size:.74rem;opacity:.65;letter-spacing:.02em}
.sl-stat .v{font-size:1.25rem;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.3}
</style>"""

_GRADE_BADGE = {
    'A_CONFIRMED': ('sl-A', 'A 주도 확인'),
    'B_EMERGING': ('sl-B', 'B 차기 후보'),
    'C_WATCH': ('sl-C', '관찰'),
    'D_LOW': ('sl-U', '후순위'),
    'UNVERIFIED': ('sl-U', '판정 불가'),
}
_IMPROVING = '<span class="sl-badge sl-A" title="1주 전보다 10EMA 위 비율 +10%p 이상, 5일 RS/ADR 상승, 거래량 확산 유지가 동시에 성립">📈 1주 전보다 10EMA·RS·거래량 모두 개선</span>'
_IMPROVING_NOTE = ('📈 배지 = 1주 전과 비교해 ① 10EMA 위 종목 비율 +10%p 이상 ② 5일 RS/ADR(변동성 감안 지수 대비 강도) 상승 '
                   '③ 거래량 실린 양봉(RVOL 1.5배↑)이 나온 종목 비율 유지 — 세 가지가 모두 성립한 섹터')


def _trend_txt(history: list | None, key: str = 'pctAbove10') -> str:
    """EMA 위 종목 비율 2주 전 → 1주 전 → 기준일. key: pctAbove10 | pctAbove21."""
    pts = [h[key] for h in (history or []) if h.get(key) is not None]
    if not pts:
        return '<span class="sl-sub">—</span>'
    out = []
    for i, p in enumerate(pts):
        txt = f'{100 * p:.0f}%'
        if i == len(pts) - 1 and len(pts) > 1:
            cls_ = 'sl-pos' if p > pts[-2] else 'sl-neg' if p < pts[-2] else ''
            txt = f'<b class="{cls_}">{txt}</b>'
        else:
            txt = f'<span class="sl-sub" style="margin:0">{txt}</span>'
        out.append(txt)
    return ' → '.join(out)
_SHORT_CHECK = {
    '점수 ≥75': '점수 75↑', '점수 ≥60': '점수 60↑',
    '5일 RS(평균) > 0': '1주 RS 플러스',
    '10EMA 위 ≥40% 또는 5일 전 대비 +20%p': '10EMA 위 40%↑',
    '10EMA 위 ≥60%': '10EMA 위 60%↑', '10EMA 3/5 유지 ≥50%': '3/5 유지 50%↑',
    '21EMA 위 ≥40%': '21EMA 위 40%↑',
}


def _esc(v) -> str:
    return html.escape(str(v))


def _signed(v, unit='%p', digits=1) -> str:
    if v is None:
        return '<span class="sl-sub">—</span>'
    cls_ = 'sl-pos' if v > 0 else 'sl-neg' if v < 0 else ''
    return f'<span class="{cls_}">{v:+.{digits}f}{unit}</span>'


def _rs_cell(v, scale) -> str:
    """부호 있는 값 + 0을 가운데 둔 막대."""
    if v is None:
        return _signed(None)
    half = 55
    w = min(abs(v) / scale, 1) * half if scale else 0
    left = half if v >= 0 else half - w
    color = '#16a34a' if v >= 0 else '#dc2626'
    return (f'{_signed(v)}<div class="sl-bar"><b style="left:{half}px"></b>'
            f'<i style="left:{left:.0f}px;width:{w:.0f}px;background:{color}"></i></div>')


def _ratio_cell(p, change_pp=None, marks=(40, 60)) -> str:
    """0~100% 막대 + 기준선(B 40%, A 60%) + 5일 전 대비 변화."""
    if p is None:
        return _signed(None)
    pct = 100 * p
    color = '#16a34a' if pct >= marks[-1] else '#f59e0b' if pct >= marks[0] else '#9ca3af'
    ticks = ''.join(f'<b style="left:{1.1 * m:.0f}px"></b>' for m in marks)
    delta = ''
    if change_pp is not None and abs(change_pp) >= 1:
        delta = f' <span class="sl-sub">{"▲" if change_pp > 0 else "▼"}{abs(change_pp):.0f}</span>'
    return (f'{pct:.0f}%{delta}<div class="sl-bar">'
            f'<i style="left:0;width:{1.1 * pct:.0f}px;background:{color}"></i>{ticks}</div>')


def _next_step(s: dict) -> str:
    """이 섹터가 한 단계 올라가려면 무엇이 부족한지."""
    if s['grade'] == 'UNVERIFIED':
        return _esc(' · '.join(s['gatingReasons'][:2]) or '데이터 부족')
    checks = s.get('gateChecks') or {}
    if s['grade'] == 'A_CONFIRMED':
        return '모든 조건 충족'
    target = 'A' if s['grade'] == 'B_EMERGING' else 'B'
    miss = [_SHORT_CHECK.get(k, k) for k, ok in checks.get(target, {}).items() if not ok]
    return f'{target}까지: ' + _esc(', '.join(miss)) if miss else ''


def _market_strip(m: Market, res: dict):
    b, cov = res['bench'], res['coverage']
    def box(k, v, tone=''):
        return f'<div class="sl-stat {tone}"><div class="k">{k}</div><div class="v">{v}</div></div>'

    parts = []      # 기준일은 오른쪽 선택 상자가 보여준다
    for n in b['names']:
        for lbl, v in (('1주', b['ret5'][n]), ('1개월', b['ret21'][n])):
            parts.append(box(f'{_esc(n)} {lbl}', _signed(v, '%'), 'pos' if v > 0 else 'neg' if v < 0 else ''))
    parts.append(box('데이터 확보', f'{cov["full"]}<span style="font-size:.8rem;opacity:.6">/{cov["total"]}</span>'))
    st.markdown(_CSS + '<div class="sl-mkt">' + ''.join(parts) + '</div>', unsafe_allow_html=True)
    if res.get('phase') is None and b['blend5'] < 0 and b['blend21'] < 0:   # 국면 안내가 있으면 중복
        st.caption(f"⚠️ 시장({'·'.join(b['names'])}{' 평균' if len(b['names']) > 1 else ''})이 1주·1개월 모두 하락 중 — 주도섹터 확정이 어렵고 포지션 규모를 줄이는 구간입니다.")


def _score_hist(s: dict) -> str:
    pts = [h['score'] for h in s.get('history') or [] if h.get('score') is not None]
    if not pts:
        return '—'
    return ' → '.join(f'<span class="sl-sub" style="margin:0">{p:.0f}</span>' for p in pts[:-1]) + \
        (' → ' if len(pts) > 1 else '') + f'<b>{pts[-1]:.0f}</b>'


def _rotation(res: dict, labels: dict, names: dict, records: dict):
    st.markdown('#### 🔄 순환매 후보 — 새로 강해지는 섹터')
    st.markdown('<div class="sl-note">2주 전엔 약했는데(점수 60 미만) 이번 주 <b>1주 RS가 지수보다 강하고</b>, '
                '그 뒤 <b>점수 +10 이상</b> 또는 <b>10EMA 위 종목 비율 +20%p 이상</b> 오른 섹터. '
                '찐반등 2~3주 뒤 처음부터 주도하지 않던 섹터가 치고 올라오면 여기서 먼저 보입니다.</div>',
                unsafe_allow_html=True)
    cands = ss.rotation_candidates(res['sectors'])
    if not cands:
        st.caption('지금은 조건에 맞는 섹터가 없습니다.')
        return
    leaders: dict = {}
    for r in sorted(res['leaders'], key=lambda r: -r['leaderScore']):
        leaders.setdefault(r['primaryTheme'], []).append(r)
    rows = []
    for s in cands:
        badge_cls, badge = _GRADE_BADGE[ss.grade_bucket(s)]
        top = ', '.join(_esc(names.get(r['ticker']) or (records.get(r['ticker']) or {}).get('companyName')
                             or r['ticker']) for r in leaders.get(s['theme'], [])[:3])
        rows.append(
            f'<tr><td><span class="sl-name">{_esc(labels[s["theme"]])}</span>'
            f'<span class="sl-sub">{s["validCount"]}종목</span>'
            + (f'<div style="margin-top:3px">{_IMPROVING}</div>' if s.get('improving') else '') + '</td>'
            f'<td><span class="sl-badge {badge_cls}">{badge}</span></td>'
            f'<td>{_score_hist(s)} <span class="sl-pos">({s["scoreRise"]:+.0f})</span></td>'
            f'<td>{_signed(s["medianRs5"])}</td><td>{_signed(s["medianRs21"])}</td>'
            f'<td>{_trend_txt(s.get("history"))}</td>'
            f'<td class="sl-need">{top}</td></tr>')
    st.markdown(
        '<div class="sl-wrap"><table class="sl-t"><thead><tr>'
        '<th>섹터</th><th>판정</th><th>점수 (2주전→1주전→기준일)</th><th>1주 RS</th><th>1개월 RS</th>'
        '<th>10EMA 위 종목 비율 (2주전→1주전→기준일)</th><th>핵심 종목</th>'
        '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>', unsafe_allow_html=True)


def _next_sectors(res: dict, labels: dict, names: dict | None = None, records: dict | None = None):
    names, records = names or {}, records or {}
    st.markdown('#### 전체 섹터 판정 — A 확인된 주도 / B 차기 후보')
    st.markdown('<div class="sl-note">하루 상승폭이나 뉴스가 아니라 <b>1주·1개월 상대강도</b>(시장 지수 대비, 종목 중앙값)와 '
                '<b>10EMA 위에서 버티는 종목 비율</b>로 판정합니다. A·B가 아니면 45점 이상 관찰, 미만 후순위. '
                f'{_IMPROVING_NOTE}.</div>',
                unsafe_allow_html=True)
    sectors = [s for s in res['sectors'] if s['inMainRanking']]
    order = {'A_CONFIRMED': 0, 'B_EMERGING': 1, 'C_WATCH': 2, 'D_LOW': 3, 'UNVERIFIED': 4}
    sectors.sort(key=lambda s: (order[ss.grade_bucket(s)], not s.get('improving'), -(s['score'] or -1)))
    vals = [abs(v) for s in sectors for v in (s['medianRs5'], s['medianRs21']) if v is not None]
    scale = max(vals) if vals else 1
    leaders: dict = {}
    for r in sorted(res['leaders'], key=lambda r: -r['leaderScore']):
        leaders.setdefault(r['primaryTheme'], []).append(r)
    rows = []
    for s in sectors:
        bucket = ss.grade_bucket(s)
        badge_cls, badge = _GRADE_BADGE[bucket]
        score = '—' if s['score'] is None else f"{s['score']:.0f}"
        chg = '' if s.get('scoreChange') is None else f'<div>{_signed(s["scoreChange"], "", 0)}</div>'
        top = ', '.join(_esc(names.get(r['ticker']) or (records.get(r['ticker']) or {}).get('companyName')
                             or r['ticker']) for r in leaders.get(s['theme'], [])[:2])
        rows.append(
            f'<tr class="{"dim" if bucket in ("UNVERIFIED", "D_LOW") else ""}">'
            f'<td><span class="sl-name">{_esc(labels[s["theme"]])}</span>'
            f'<span class="sl-sub">{s["validCount"]}종목</span>'
            + (f'<div style="margin-top:3px">{_IMPROVING}</div>' if s.get('improving') else '') + '</td>'
            f'<td><span class="sl-badge {badge_cls}">{badge}</span></td>'
            f'<td><b>{score}</b>{chg}</td>'
            f'<td>{_rs_cell(s["medianRs5"], scale)}</td>'
            f'<td>{_rs_cell(s["medianRs21"], scale)}</td>'
            f'<td>{_ratio_cell(s["pctAbove10"])}<div class="sl-sub" style="margin:2px 0 0">{_trend_txt(s.get("history"))}</div></td>'
            f'<td>{_ratio_cell(s["pctEma10Persist3Of5"], marks=(50,))}</td>'
            f'<td class="sl-need">{top}</td>'
            f'<td class="sl-need">{_next_step(s)}</td></tr>')
    st.markdown(
        '<div class="sl-wrap"><table class="sl-t"><thead><tr>'
        '<th>섹터</th><th>판정</th><th>점수 (전주 대비)</th><th>1주 RS</th><th>1개월 RS</th>'
        '<th>10EMA 위 비율 (2주 전 → 지금)</th><th>10EMA 3/5일 유지</th><th>핵심 주도주</th><th>올라가려면</th>'
        '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>', unsafe_allow_html=True)


def _stock_rows_html(rows: list, labels: dict, names: dict, records: dict, grade_of: dict) -> str:
    vals = [abs(v) for r in rows for v in (r['rs5Blend'], r['rs21Blend'])]
    scale = max(vals) if vals else 1
    out = []
    for r in rows:
        t = r['ticker']
        name = names.get(t) or (records.get(t) or {}).get('companyName', '')
        title = (f'<span class="sl-name">{_esc(name)}</span><span class="sl-sub">{_esc(t)}</span>'
                 if name else f'<span class="sl-name">{_esc(t)}</span>')
        g = grade_of.get(r['primaryTheme'])
        mark = (f' <span class="sl-badge {_GRADE_BADGE[g][0]}">{g[0]}</span>'
                if g in ('A_CONFIRMED', 'B_EMERGING') else '')
        detail = (records.get(t) or {}).get('detail', '')
        dots = '●' * r['ema10DaysOfLast5'] + '○' * (5 - r['ema10DaysOfLast5'])
        out.append(
            f'<tr><td>{title}</td>'
            f'<td>{_esc(labels.get(r["primaryTheme"], ""))}{mark}<div class="sl-sub" style="margin:0">{_esc(detail)}</div></td>'
            f'<td>{_rs_cell(r["rs5Blend"], scale)}</td>'
            f'<td>{_rs_cell(r["rs21Blend"], scale)}</td>'
            f'<td>{_signed(r["nrs5"], "", 2)}</td>'
            f'<td><span class="{"sl-pos" if r["aboveEma10"] else "sl-neg"}">{dots}</span></td>'
            f'<td>{"✓" if r["hasBullishRvol15InLast5"] else ""}</td>'
            f'<td><b>{r["leaderScore"]:.0f}</b></td></tr>')
    return ('<div class="sl-wrap"><table class="sl-t"><thead><tr>'
            '<th>종목</th><th>섹터 · 테마</th><th>1주 RS</th><th>1개월 RS</th><th>1주 RS/ADR</th>'
            '<th>10EMA 최근 5일</th><th>양봉 렐볼</th><th>점수</th>'
            '</tr></thead><tbody>' + ''.join(out) + '</tbody></table></div>')


def _next_stocks(m: Market, res: dict, labels: dict, names: dict):
    st.markdown('#### 종목')
    grade_of = {s['theme']: s['grade'] for s in res['sectors']}
    records = cls.load_store(m.key)
    rot = {s['theme'] for s in ss.rotation_candidates(res['sectors'])}
    groups = {
        '순환매 섹터 종목': ('위 순환매 후보 섹터에 속한 종목',
                       [r for r in res['leaders'] if r['primaryTheme'] in rot]),
        '차기 주도주 후보': ('1주 RS가 플러스로 돌아서고 10EMA 위로 올라왔지만, 1개월 RS나 50일선은 아직 회복 전인 종목',
                       [r for r in res['leaders'] if r['leaderType'] in ('EMERGING', 'REBOUND')]),
        '이미 강한 주도주': ('1주·1개월 RS 모두 플러스이고 10·21EMA·50SMA 위에 있는 종목',
                       [r for r in res['leaders'] if r['leaderType'] == 'LEADER_TREND']),
    }
    pick = st.segmented_control('종목 구분', list(groups), default='순환매 섹터 종목',
                                key=f'{m.key}_stock_group', label_visibility='collapsed') or '순환매 섹터 종목'
    desc, rows = groups[pick]
    rows = sorted(rows, key=lambda r: -r['leaderScore'])[:20]
    st.markdown(f'<div class="sl-note">{_esc(desc)} · 주도주 점수순 상위 {len(rows)}개. '
                'A·B 배지는 그 종목의 섹터 판정. 점수는 매수 신호가 아니며, 5분봉 렐볼 기준봉 확인 전까지는 관찰만.</div>',
                unsafe_allow_html=True)
    if not rows:
        st.caption('해당 종목 없음')
        return
    st.markdown(_stock_rows_html(rows, labels, names, records, grade_of), unsafe_allow_html=True)


# ── 국면별 차기 주도섹터 · 차기 주도주 ─────────────────────

_CARD_CSS = """<style>
.nl-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:6px 0 14px}
.nl-card{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column}
.nl-badge-row{min-height:26px;margin:-4px 0 6px}
.nl-card.top{border-color:#16a34a;box-shadow:0 0 0 1px #16a34a inset}
.nl-rank{font-size:.72rem;font-weight:700;letter-spacing:.04em;opacity:.55}
.nl-title{font-size:1.15rem;font-weight:700;margin:2px 0 8px}
.sl-off{font-size:.7rem;color:#b45309;margin-top:1px}
.nl-head{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
.nl-score{font-size:1.4rem;font-weight:800;font-variant-numeric:tabular-nums}
.nl-line{font-size:.82rem;margin:3px 0;display:flex;justify-content:space-between;gap:8px}
.nl-line span:first-child{opacity:.6}
.nl-stocks{font-size:.8rem;margin-top:auto;padding-top:8px;border-top:1px solid rgba(128,128,128,.15)}
.nl-phase{border-radius:10px;padding:12px 16px;margin:4px 0 14px;font-size:.92rem;line-height:1.55}
.nl-phase.correction{background:rgba(220,38,38,.08);border-left:4px solid #dc2626}
.nl-phase.rebound{background:rgba(245,158,11,.10);border-left:4px solid #f59e0b}
.nl-phase.uptrend{background:rgba(22,163,74,.08);border-left:4px solid #16a34a}
.nl-view{font-size:.8rem;opacity:.8;margin:-6px 0 14px;padding:8px 12px;border-radius:8px;
  background:rgba(128,128,128,.08)}
.nl-score-bar{display:inline-block;vertical-align:middle;height:6px;border-radius:3px;background:#16a34a;margin-left:6px}
</style>"""

_DEFENSIVE = {'FINANCE', 'HOLDING_OTHER', 'COSMETICS_CONSUMER', 'TELECOM_NETWORK', 'CONSUMER_OTHER'}
_STAR = '★'


def _phase_banner(m: Market, res: dict):
    ph = res['phase']
    idx = m.phase_index
    manual = ' · 수동 설정' if ph.get('manual') else ''
    if ph['mode'] == 'correction':
        sp = ph['indexSincePeak']
        body = (f"<b>🔴 조정 중 ({ph['label']}{manual})</b> — 기산점 {ph['peakDate']:%m/%d} 이후 {idx} "
                f"<b>{sp:+.1f}%</b>.<br>지금은 주도섹터가 '확정'될 수 없는 구간입니다. 대신 <b>지수가 빠지는 동안 "
                "덜 빠지고, 고점을 지키고, 먼저 저점을 찍은</b> 섹터·종목을 찾습니다 — 찐반등이 나오면 이들이 먼저 튑니다.")
    elif ph['mode'] == 'rebound':
        sj = ph['indexSinceJjin']
        body = (f"<b>🟡 찐반등 {ph['label']}{manual}</b> — 기산점 {ph['peakDate']:%m/%d} · 찐반등일 {ph['jjinDate']:%m/%d} 이후 {idx} <b>{sj:+.1f}%</b>.<br>"
                "조정 중 버틴 종목 가운데 <b>찐반등 이후 지수보다 먼저 치고 나가는</b> 종목이 진짜 주도주입니다. "
                "회복 속도에 가장 큰 비중을 둡니다.")
    else:
        last = (f" — 마지막 찐반등 {ph['jjinDate']:%m/%d} 이후 {idx} <b>{ph['indexSinceJjin']:+.1f}%</b>"
                if ph.get('jjinDate') is not None and ph.get('indexSinceJjin') is not None else '')
        body = (f"<b>🟢 상승 추세</b>{last}.<br>차기 주도섹터·주도주는 다음 조정(21EMA 이탈) 전까지 마지막 기산점·찐반등 기준을 "
                "그대로 이어 갑니다. 찐반등 2주 뒤부터 새로 치고 올라오는 섹터는 아래 <b>'🔄 순환매'</b>에서 봅니다.")
    st.markdown(_CARD_CSS + f'<div class="nl-phase {ph["mode"]}">{body}</div>', unsafe_allow_html=True)


def _phase_title(m: Market, ph: dict | None) -> str:
    """시장 현황 접기 제목 — 접어 둬도 국면이 보이게."""
    if ph is None:
        return '**📊 시장 현황**'
    tag = {'correction': f"🔴 조정 중 ({ph['label']})", 'rebound': f"🟡 찐반등 {ph['label']}"}.get(ph['mode'], '🟢 상승 추세')
    return f"**📊 시장 현황 · {m.phase_index} {tag}{' · 수동 설정' if ph.get('manual') else ''}**"


def _rs_cols(ph: dict) -> list:
    """RS/ADR 표시 구간 — [(라벨, 섹터 키, 섹터 %p 키, 종목 키, 종목 %p 키)].
    조정 중: 기산점 ~ 기준일 한 줄. 찐반등 이후: 기산점 ~ 찐반등(조정 구간) + 찐반등 ~ 기준일(반등 구간) 두 줄."""
    # 항상 두 줄: ① 하락국면(기산점 ~ 찐반등, 찐반등 전이면 ~ 기준일) ② 상승국면(찐반등 ~ 기준일, 찐반등 전이면 —)
    p, j = ph.get('peakDate'), ph.get('jjinDate')
    ps = f'기산점 {p:%m/%d}' if p is not None else '기산점'
    if j is not None:     # 찐반등 이후·상승 추세 모두 (다음 조정 전까지 마지막 찐반등 유지)
        down = f'하락국면 RS/ADR ({ps} ~ 찐반등 {j:%m/%d})'
        up = (f'상승국면 RS/ADR (찐반등 {j:%m/%d} ~ 기준일)',
              'medianSinceJjinAdr', 'medianSinceJjin', 'sinceJjinExcessAdr', 'sinceJjinExcess')
    else:
        down = f'하락국면 RS/ADR ({ps} ~ 기준일)'
        up = ('상승국면 RS/ADR (찐반등 전)', None, None, None, None)
    return [(down, 'medianSincePeakAdr', 'medianSincePeak', 'sincePeakExcessAdr', 'sincePeakExcess'), up]


def _rs_val(ph_col: tuple, x: dict, sector: bool) -> str:
    _, sa, sr, ka, kr = ph_col
    if sa is None:          # 찐반등 전 — 상승국면 값 없음
        return _signed(None)
    return _adr_vs(x.get(sa), x.get(sr)) if sector else _adr_vs(x.get(ka), x.get(kr))


def _adr_vs(adr, raw) -> str:
    """RS/ADR 값만 표시 (raw %p는 헷갈려서 화면에 안 씀)."""
    return _signed(adr, '', 2)


_TREND_FILE = {'US': 'us.tickers', 'KR_KOSPI': 'kospi.tickers', 'KR_KOSDAQ': 'kosdaq.tickers'}


def _trend_tickers(m: Market) -> set:
    """추세추종 탭(코스피/코스닥/나스닥) 와치리스트. 주도섹터 유니버스는 10EMA 목록·210종목도 포함하므로 구분 표시용."""
    p = _SAVED / _TREND_FILE[m.key]
    if not p.exists():
        return set()
    return {x.strip() for x in p.read_text(encoding='utf-8').splitlines() if x.strip()}


def _stock_name(t: str, names: dict, records: dict) -> str:
    return names.get(t) or (records.get(t) or {}).get('companyName', '') or t


def _lead_sectors(m: Market, res: dict, labels: dict, names: dict):
    ph, ranked, thin = res['phase'], res['nextSectors'], res['thinSectors']
    records = cls.load_store(m.key)
    if not ranked:
        st.caption('3종목 이상인 섹터가 없어 순위를 매길 수 없습니다.')
        return
    cols = _rs_cols(ph)
    in_trend = _trend_tickers(m)
    trend = {s['theme']: s for s in res['sectors']}     # 10EMA 위 비율 3주 추이·동시 개선
    cards = []
    for i, s in enumerate(ranked[:3]):
        t = trend.get(s['theme'], {})
        stocks = ', '.join(
            f"{_esc(_stock_name(r['ticker'], names, records))} {r['nextScore']:.0f}"
            + ('' if r['ticker'] in in_trend else ' <span class="sl-off">(추세추종 탭에 없음)</span>')
            for r in s['top'])
        lines = [(c[0], _rs_val(c, s, True)) for c in cols] + [
                 ('최근 3개월 고점 대비 (중앙값)', _signed(s['medianOffHigh'], '%')),
                 ('10EMA 위 종목 비율 (2주전→1주전→기준일)', _trend_txt(t.get('history'))),
                 ('21EMA 위 종목 비율 (2주전→1주전→기준일)', _trend_txt(t.get('history'), 'pctAbove21')),
                 ('강한 종목 (70점↑)', f"{round(s['strongShare'] * s['n'])}/{s['n']}")]
        body = ''.join(f'<div class="nl-line"><span>{a}</span><span>{b}</span></div>' for a, b in lines)
        # 배지가 없어도 같은 높이를 비워 둬서 카드끼리 줄 위치가 맞게
        badge = f'<div class="nl-badge-row">{_IMPROVING if t.get("improving") else ""}</div>'
        cards.append(f'<div class="nl-card {"top" if i == 0 else ""}"><div class="nl-head"><div>'
                     f'<div class="nl-rank">{i + 1}위</div><div class="nl-title">{_esc(labels[s["theme"]])}</div></div>'
                     f'<div class="nl-score">{s["score"]:.0f}</div></div>'
                     f'{badge}{body}<div class="nl-stocks"><span style="opacity:.6">대표 </span>{stocks}</div></div>')
    st.markdown('<div class="nl-cards">' + ''.join(cards) + '</div>', unsafe_allow_html=True)

    # 제 판단 — 조정장 상위에 방어 섹터가 섞이면 반등장 주도주가 아닐 수 있다
    notes = []
    defensive = [labels[s['theme']] for s in ranked[:3] if s['theme'] in _DEFENSIVE]
    if ph['mode'] == 'correction':
        notes.append('조정장에서는 방어 성격 섹터가 덜 빠져 상위에 오르기 쉽습니다. '
                     '<b>찐반등 후 3~5일 안에 고점 쪽으로 먼저 가는 섹터</b>가 진짜 주도섹터입니다.')
    if defensive:
        notes.append(f"상위 3개 중 <b>{', '.join(defensive)}</b>는 방어 성격이 강해 반등장 주도섹터가 되기보다 "
                     '"덜 빠진" 결과일 가능성이 큽니다. 4위 이하 성장 섹터도 같이 보세요.')
    if notes:
        st.markdown('<div class="nl-view">💬 ' + '<br>💬 '.join(notes) + '</div>', unsafe_allow_html=True)

    rest = ranked[3:]
    if rest:
        rows = ''.join(
            f'<tr><td>{i + 4}</td><td><span class="sl-name">{_esc(labels[s["theme"]])}</span>'
            f'<span class="sl-sub">{s["n"]}종목</span></td><td><b>{s["score"]:.0f}</b></td>'
            + ''.join(f'<td>{_rs_val(c, s, True)}</td>' for c in cols)
            + f'<td>{_signed(s["medianOffHigh"], "%")}</td>'
            f'<td>{_trend_txt(trend.get(s["theme"], {}).get("history"))}'
            + (f' {_IMPROVING}' if trend.get(s['theme'], {}).get('improving') else '') + '</td>'
            f'<td>{_trend_txt(trend.get(s["theme"], {}).get("history"), "pctAbove21")}</td>'
            f'<td class="sl-need">{", ".join(_esc(_stock_name(r["ticker"], names, records)) for r in s["top"])}</td></tr>'
            for i, s in enumerate(rest))
        st.markdown(
            '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>순위</th><th>섹터</th><th>점수</th>'
            + ''.join(f'<th>{c[0]}</th>' for c in cols) + '<th>최근 3개월 고점 대비 (중앙값)</th><th>10EMA 위 종목 비율 (2주전→1주전→기준일)</th><th>21EMA 위 종목 비율 (2주전→1주전→기준일)</th><th>대표주</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>', unsafe_allow_html=True)
    if thin:
        st.caption('표본 부족(3종목 미만)으로 순위 제외: '
                   + ', '.join(f"{labels[s['theme']]}({s['n']})" for s in thin))


def _lead_stocks(m: Market, res: dict, labels: dict, names: dict):
    ph, picks = res['phase'], res['picks']
    records = cls.load_store(m.key)
    if not picks:
        st.caption('점수를 낼 수 있는 종목이 없습니다.')
        return
    top_sectors = {s['theme'] for s in res['nextSectors'][:3]}
    rebound = ph.get('scoring', ph['mode']) == 'rebound'
    cols = _rs_cols(ph)
    in_trend = _trend_tickers(m)
    only_top =st.toggle('상위 3개 섹터 종목만', key=f'{m.key}_only_top')
    rows = sorted((r for r in picks if not only_top or r['primaryTheme'] in top_sectors),
                  key=lambda r: -r['nextScore'])[:20]
    body = []
    for r in rows:
        t = r['ticker']
        nm = _stock_name(t, names, records)
        title = (f'<span class="sl-name">{_esc(nm)}</span><span class="sl-sub">{_esc(t)}</span>'
                 if nm != t else f'<span class="sl-name">{_esc(t)}</span>')
        if t not in in_trend:
            title += '<div class="sl-off">추세추종 탭에 없음</div>'
        star = f' <span style="color:#16a34a">{_STAR}</span>' if r['primaryTheme'] in top_sectors else ''
        detail = (records.get(t) or {}).get('detail', '')
        lead = r.get('leadDays')
        lead_txt = '—' if lead is None else (f'{lead}일 먼저' if lead > 0 else '같이' if lead == 0 else f'{-lead}일 늦게')
        body.append(
            f'<tr><td>{title}</td>'
            f'<td>{_esc(labels.get(r["primaryTheme"], ""))}{star}<div class="sl-sub" style="margin:0">{_esc(detail)}</div></td>'
            + ''.join(f'<td>{_rs_val(c, r, False)}</td>' for c in cols)
            + f'<td>{_signed(r.get("offHigh"), "%")}</td><td>{lead_txt}</td>'
            + ('<td>—</td>' if r.get('volRatio') is None else f'<td>{r["volRatio"]:.1f}배</td>')
            +
            f'<td><span class="{"sl-pos" if r["aboveEma21"] else "sl-neg"}">{"위" if r["aboveEma21"] else "아래"}</span></td>'
            f'<td><b>{r["nextScore"]:.0f}</b><span class="nl-score-bar" style="width:{0.5 * r["nextScore"]:.0f}px"></span></td></tr>')
    st.markdown(
        '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>종목</th><th>섹터 · 테마</th>'
        + ''.join(f'<th>{c[0]}</th>' for c in cols) + '<th>최근 3개월 고점 대비</th><th>저점</th><th>상승/하락 거래량</th>'
        '<th>21EMA (기준일)</th><th>점수</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>',
        unsafe_allow_html=True)
    parts = ' + '.join(f'{k} {v}' for k, v in (
        {'찐반등 후 회복': 25, '덜 빠짐': 20, '고점 근접': 20, '21EMA 위': 15, '거래량 우위': 10, '양봉 렐볼': 10}
        if rebound else
        {'덜 빠짐': 30, '고점 근접': 25, '저점 선행': 15, '거래량 우위': 15, '21EMA 위': 15}).items())
    st.caption(f'{_STAR} = 상위 3개 섹터 소속. 점수(100) = {parts} — 같은 와치리스트 안 상대 순위. '
               '매수 신호가 아닙니다: 찐반등 확인 후 5분봉 렐볼 기준봉에서 진입.')


# ── 상세 보기 ─────────────────────────────────────────────

def _sector_table(sectors: list, labels: dict):
    rows = [{
        '섹터': labels[s['theme']], '등급': GRADE_LABEL[s['grade']],
        '점수': None if s['score'] is None else round(s['score'], 1),
        '확보': f"{s['validCount']}/{s['memberCount']}",
        '5일 수익률%': s['medianRet5'], '21일 수익률%': s['medianRet21'],
        '5일 RS%p': s['medianRs5'], '21일 RS%p': s['medianRs21'],
        '5일 RS/ADR': s['medianNrs5'], '21일 RS/ADR': s['medianNrs21'],
        '10EMA 위%': None if s['pctAbove10'] is None else 100 * s['pctAbove10'],
        '10EMA 변화%p': s['ema10ChangePp'],
        '3/5 유지%': None if s['pctEma10Persist3Of5'] is None else 100 * s['pctEma10Persist3Of5'],
        '21EMA 위%': None if s['pctAbove21'] is None else 100 * s['pctAbove21'],
        'RVOL 확산%': None if s['pctBullishRvol15_5D'] is None else 100 * s['pctBullishRvol15_5D'],
        '판정 보류 사유': ' / '.join(s['gatingReasons']),
    } for s in sectors]
    if not rows:
        st.caption('해당 섹터 없음')
        return
    fmt = {k: st.column_config.NumberColumn(format='%.2f') for k in
           ['5일 수익률%', '21일 수익률%', '5일 RS%p', '21일 RS%p', '5일 RS/ADR', '21일 RS/ADR']}
    fmt.update({k: st.column_config.NumberColumn(format='%.0f') for k in
                ['10EMA 위%', '10EMA 변화%p', '3/5 유지%', '21EMA 위%', 'RVOL 확산%']})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', column_config=fmt)


def _heatmap(m: Market, sectors: list, labels: dict):
    fig = go.Figure()
    for s in sectors:
        if s['pctAbove10'] is None or s['medianNrs5'] is None:
            continue
        if s['score'] is None:
            color = '#9aa3ad'
        else:
            color = '#1a9e6f' if (s['ema10ChangePp'] or 0) > 0 else '#c4554d'
        fig.add_trace(go.Scatter(
            x=[100 * s['pctAbove10']], y=[s['medianNrs5']], mode='markers+text',
            text=[labels[s['theme']]], textposition='top center',
            marker=dict(size=10 + 2 * min(s['validCount'], 20), color=color, opacity=0.8,
                        line=dict(width=1, color='white')),
            hovertemplate=(f"{labels[s['theme']]}<br>10EMA 위 %{{x:.0f}}%<br>5일 RS/ADR %{{y:.2f}}"
                           f"<br>확보 {s['validCount']}/{s['memberCount']}"
                           f"<br>{GRADE_LABEL[s['grade']]}<extra></extra>"),
            showlegend=False))
    fig.add_vline(x=60, line_dash='dot', line_color='#888')
    fig.add_hline(y=0, line_dash='dot', line_color='#888')
    fig.update_layout(height=460, margin=dict(l=10, r=10, t=30, b=10),
                      xaxis_title='10EMA 위 종목 비율 (%)', yaxis_title='5일 RS/ADR 중앙값')
    st.plotly_chart(fig, width='stretch', key=f'{m.key}_heat')
    st.caption('초록 = 10EMA 위 비율이 5일 전보다 늘어남, 빨강 = 줄어듦, 회색 = 점수 미산출(미검증). 원 크기 = 데이터 확보 종목 수.')


def _stock_frame(m: Market, rows: list, labels: dict, names: dict) -> pd.DataFrame:
    """섹터 = 주력 테마, 테마 = 세부 표현 — 추세추종·10EMA 탭과 같은 기준."""
    records = cls.load_store(m.key)
    out = []
    for r in rows:
        row = {'티커': r['ticker']}
        if names:
            row['종목명'] = names.get(r['ticker'], '')
        row.update({
            '섹터': labels.get(r.get('primaryTheme'), '미분류'),
            '테마': (records.get(r['ticker']) or {}).get('detail', ''),
            '유형': TYPE_LABEL.get(r.get('leaderType'), ''),
            '주도주 점수': None if r.get('leaderScore') is None else round(r['leaderScore'], 1),
            '상태': STATUS_LABEL.get(r['status'], r['status']),
            '종가': r.get('close'), '5일%': r.get('ret5'), '21일%': r.get('ret21'),
            'ADR20%': r.get('adr20Pct'),
            '5일 RS%p': r.get('rs5Blend'), '21일 RS%p': r.get('rs21Blend'),
            '5일 RS/ADR': r.get('nrs5'), '21일 RS/ADR': r.get('nrs21'),
            '섹터내 5일 RS%p': r.get('rsSector5'),
            '10EMA': None if 'aboveEma10' not in r else ('위' if r['aboveEma10'] else '아래'),
            '21EMA': None if 'aboveEma21' not in r else ('위' if r['aboveEma21'] else '아래'),
            '50SMA': None if r.get('aboveSma50') is None else ('위' if r['aboveSma50'] else '아래'),
            '10EMA 유지(5일)': r.get('ema10DaysOfLast5'),
            'RVOL20': r.get('rvol20'),
            '양봉 RVOL≥1.5 (5일)': None if 'hasBullishRvol15InLast5' not in r else
                                   ('있음' if r['hasBullishRvol15InLast5'] else '없음'),
            f'20일 평균 거래대금({m.value_unit})': None if r.get('avgValue20') is None
                                               else r['avgValue20'] / m.value_div,
            '경고': ', '.join((r.get('flags') or [])
                              + ([] if r.get('liquidityOk', True) else ['유동성 기준 미달'])),
            '사유': r.get('missingReason', ''),
        })
        out.append(row)
    return pd.DataFrame(out)


def _stock_fmt(m: Market) -> dict:
    fmt = {k: st.column_config.NumberColumn(format='%.2f') for k in
           ['5일%', '21일%', 'ADR20%', '5일 RS%p', '21일 RS%p', '5일 RS/ADR', '21일 RS/ADR',
            '섹터내 5일 RS%p', 'RVOL20']}
    fmt['종가'] = st.column_config.NumberColumn(format='%.2f' if m.key == 'US' else '%.0f')
    fmt[f'20일 평균 거래대금({m.value_unit})'] = st.column_config.NumberColumn(format='%.0f')
    return fmt


def _drilldown(m: Market, res: dict, labels: dict, names: dict):
    sectors = res['sectors']
    if not sectors:
        st.caption('분류된 섹터가 없습니다.')
        return
    opts = {f"{labels[s['theme']]} ({GRADE_LABEL[s['grade']]})": s for s in sectors}
    pick = st.selectbox('섹터 선택', list(opts), key=f'{m.key}_drill')
    s = opts[pick]
    by_ticker = {r['ticker']: r for r in res['stocks']}
    members = [by_ticker[t] for t in s['members'] if t in by_ticker]
    members.sort(key=lambda r: -(r.get('leaderScore') or -1))
    st.caption(f"구성 {s['memberCount']}종목, 데이터 확보 {s['validCount']}종목. 구성은 분류 기록(주력 테마) 기준입니다.")
    st.dataframe(_stock_frame(m, members, labels, names), hide_index=True, width='stretch',
                 column_config=_stock_fmt(m))


def _leaderboards(m: Market, res: dict, labels: dict, names: dict):
    leaders = res['leaders']
    if not leaders:
        st.warning('주도주 점수를 낼 수 있는 종목이 없습니다 (메인 테마로 분류된 정상 데이터 종목 없음).')
        return
    f = st.columns(4)
    themes = sorted({labels[r['primaryTheme']] for r in leaders})
    pick_theme = f[0].multiselect('섹터', themes, key=f'{m.key}_lb_theme')
    adr_rng = f[1].slider('ADR20 %', 0.0, 20.0, (0.0, 20.0), 0.5, key=f'{m.key}_lb_adr')
    min_nrs = f[2].number_input('5일 RS/ADR 최소', value=-10.0, step=0.5, key=f'{m.key}_lb_nrs')
    ema_only = f[3].checkbox('10EMA 위만', key=f'{m.key}_lb_ema')

    def ok(r):
        return ((not pick_theme or labels[r['primaryTheme']] in pick_theme)
                and adr_rng[0] <= r['adr20Pct'] <= adr_rng[1]
                and r['nrs5'] >= min_nrs and (not ema_only or r['aboveEma10']))

    tabs = st.tabs([TYPE_LABEL[k] for k in ('LEADER_TREND', 'EMERGING', 'REBOUND')] + ['전체 통합 (참고)'])
    for tab, key in zip(tabs, ('LEADER_TREND', 'EMERGING', 'REBOUND', None)):
        with tab:
            rows = [r for r in leaders if ok(r) and (key is None or r['leaderType'] == key)]
            rows.sort(key=lambda r: -r['leaderScore'])
            st.dataframe(_stock_frame(m, rows, labels, names), hide_index=True, width='stretch',
                         column_config=_stock_fmt(m))
    st.caption('주도주 점수 85점 만점 = 5일 RS 20 + 21일 RS 20 (시장·섹터 대비, 원시 절반·ADR 보정 절반) '
               '+ 추세 구조 20 + 10EMA 유지 15 + 거래량·유동성 10. 실적·계약 촉매 15점은 데이터 소스 미연결로 '
               '반영하지 않습니다. 점수가 높아도 매수 신호가 아닙니다 — 5분봉 렐볼 기준봉 확인 전에는 관찰만.')


def _github_commit(path: pathlib.Path, message: str) -> bool:
    try:
        token = st.secrets.get('GITHUB_TOKEN', '')
    except Exception:
        token = ''
    if not token:
        return False
    rel = path.relative_to(_ROOT).as_posix()
    url = f'https://api.github.com/repos/{_GITHUB_REPO}/contents/{rel}'
    hdrs = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}
    try:
        r = requests.get(url, headers=hdrs, timeout=5)
        body = {'message': message, 'content': base64.b64encode(path.read_bytes()).decode()}
        if r.ok:
            body['sha'] = r.json().get('sha')
        return requests.put(url, json=body, headers=hdrs, timeout=15).ok
    except Exception:
        return False


def _classification_editor(m: Market, labels: dict, names: dict):
    uni = _universe(m)
    records = cls.load_store(m.key)
    rows = []
    for t, legacy in uni:
        rec = records.get(t) or {}
        row = {'티커': t, '회사': names.get(t) or rec.get('companyName', ''),
               '주력 테마': labels.get(rec.get('primaryTheme', 'UNCLASSIFIED')),
               '제안(미승인)': labels.get(rec.get('suggestedTheme')) if rec.get('suggestedTheme') else '',
               '보조 태그': ', '.join(labels.get(c, c) for c in rec.get('secondaryThemes', [])),
               '신뢰도': rec.get('confidence', '미분류'),
               '확정': '사용자' if rec.get('overrideByUser') else ('자동' if rec else '없음')}
        if m.key == 'US':
            row['기존 수동 분류'] = legacy
        rows.append(row)
    df = pd.DataFrame(rows)
    if st.checkbox('미분류·제안만 보기', key=f'{m.key}_cls_unc') and not df.empty:
        df = df[df['주력 테마'] == labels['UNCLASSIFIED']]
    st.dataframe(df, hide_index=True, width='stretch', height=360)

    tickers = [t for t, _ in uni]
    if not tickers:
        return
    t = st.selectbox('종목 상세·수정', tickers, key=f'{m.key}_cls_pick',
                     format_func=lambda x: f"{x} {names.get(x, '')}".strip())
    rec = records.get(t) or {}
    if rec:
        st.markdown(f"**{rec.get('companyName') or names.get(t) or t}** — {rec.get('businessSummary', '')}")
        st.caption(f"근거: {rec.get('why', '')}")
        for u in rec.get('evidenceUrls', []):
            st.caption(f'- {u}')
        if rec.get('gateReasons'):
            st.caption('미승인 사유: ' + ', '.join(rec['gateReasons']))
        st.caption(f"검토 {rec.get('reviewedAt', '')} · {rec.get('method', '')} · {rec.get('taxonomyVersion', '')}")
    else:
        st.caption('분류 기록 없음 — 다음 배치에서 자동 분류됩니다.')

    codes = list(labels)
    with st.form(f'{m.key}_cls_form_{t}'):
        cur = rec.get('primaryTheme') if rec.get('primaryTheme') != 'UNCLASSIFIED' else rec.get('suggestedTheme')
        primary = st.selectbox('주력 테마', codes,
                               index=codes.index(cur) if cur in codes else codes.index('UNCLASSIFIED'),
                               format_func=lambda c: labels[c])
        secondary = st.multiselect('보조 태그', [c for c in codes if c != 'UNCLASSIFIED'],
                                   default=[c for c in rec.get('secondaryThemes', []) if c in codes],
                                   format_func=lambda c: labels[c])
        reason = st.text_input('수정 이유 (필수)')
        if st.form_submit_button('확정 저장 — 이후 자동 분류가 덮어쓰지 않음'):
            if not reason.strip():
                st.error('수정 이유를 적어 주세요.')
            else:
                recs = dict(records)
                cls.set_override(m.key, t, primary, secondary, reason, recs)
                path = cls.save_store(m.key, recs)
                pushed = _github_commit(path, f'data: {t} 테마 수동 확정')
                st.cache_data.clear()
                st.success('저장했습니다.' + ('' if pushed else ' (GitHub 토큰이 없어 이 서버에만 저장됨)'))


def _methodology(m: Market, res: dict):
    names = res['bench']['names']
    pair = '·'.join(names)
    rs_def = (f'{names[0]}, {names[1]}, 둘의 평균에 대해 각각 계산 (%p)' if len(names) > 1
              else f'{names[0]} 지수 대비로 계산 (%p)')
    st.markdown(f"""
**수익률·RS** — `R_N = 100 × (C_t / C_(t−N) − 1)`, N = 5·21거래일. `RS = R_종목 − R_벤치`를 {rs_def}.
섹터 수익률은 구성 종목 **중앙값** — 한 종목 급등이 섹터 전체를 끌어올리지 않습니다.

**ADR20 보정 RS** — `ADR20 = 최근 20일 평균 100 × (고가 − 저가) / 저가`, `RS/ADR = RS(평균 대비) / ADR20`. 섹터 값은 종목별 보정값의 중앙값. 5일과 21일은 서로 직접 비교하지 않고 각자 섹터 간 백분위로 점수화.

**추세** — 10EMA·21EMA(지수이동평균)와 50SMA(단순이동평균)를 각 거래일마다 따로 계산. "3/5 유지" = 최근 5거래일 중 3일 이상 그날의 10EMA 위 종가.

**거래량** — `RVOL20 = 당일 거래량 / 직전 20일 평균` (당일 제외). RVOL 확산 = 최근 5일 중 **양봉(종가 > 시가)**이면서 RVOL20 ≥ 1.5인 날이 있었던 종목 비율.

**섹터 100점** — 5일 RS 10 + 5일 RS/ADR 10 + 21일 RS 10 + 21일 RS/ADR 10 (섹터 간 백분위, 최하위 0·최상위 만점) + 10EMA 위 20 + 3/5 유지 15 + 21EMA 위 10 + RVOL 확산 10 + 5일 플러스 종목 5.

**판정 게이트** — 구성 5종목 이상, 데이터 확보 80% 이상이어야 점수를 냅니다. **A** = 75점 이상 + 5일·21일 섹터 수익률이 {pair} {'모두 ' if len(names) > 1 else ''}초과 + 10EMA 위 60% + 3/5 유지 50% + 21EMA 위 40%. **B** = 60점 이상 + 5일 RS 양수 + (10EMA 위 40% 또는 5일 전 대비 +20%p).
뉴스·실적은 점수에 넣지 않습니다.
""")
    if m.key == 'US':
        st.caption('유니버스: us_innovation_210 (원본 워치리스트 고정 210종목, 외부 발견 종목 제외). 유동성 기준 20일 평균 100만주.')
    else:
        st.caption('유니버스: 현재 와치리스트 + 10EMA 목록. 와치리스트를 새로 올리면 섹터 구성이 바뀌므로 '
                   '과거 기준일 재현은 "현재 구성 종목의 과거 시세" 기준입니다. 유동성 기준 20일 평균 거래대금 50억원.')
    cov = res['coverage']
    st.markdown(f"**데이터** — 배치 수집 스냅샷 일봉. 기준일 {res['asof']} 이후 데이터는 계산에 쓰지 않습니다. "
                f"확보 {cov['full']}/{cov['total']}, 상태별: "
                + ', '.join(f"{STATUS_LABEL.get(k, k)} {v}" for k, v in cov['byStatus'].items()))
    bad = [r for r in res['stocks'] if r['status'] != 'FULL']
    if bad:
        st.dataframe(pd.DataFrame([{'티커': r['ticker'], '상태': STATUS_LABEL.get(r['status'], r['status']),
                                    '사유': r.get('missingReason', ''), '마지막 일봉': r.get('lastDate')}
                                   for r in bad]), hide_index=True, width='stretch')
    warn = [r for r in res['stocks'] if r.get('flags')]
    if warn:
        st.caption('경고: ' + ', '.join(f"{r['ticker']}({'/'.join(r['flags'])})" for r in warn))
    snap = store.load_snapshot(m.key)
    st.caption(f"스냅샷 수집 {snap.get('fetched_at', '?')} · 분류 테마 목록 {res['taxonomyVersion']}")


def _downloads(m: Market, res: dict, labels: dict, names: dict):
    c = st.columns(2)
    sec = pd.DataFrame([{**{k: v for k, v in s.items()
                            if k not in ('members', 'scoreParts', 'gateChecks', 'invalidMembers')},
                         'themeLabel': labels[s['theme']]} for s in res['sectors']])
    c[0].download_button('섹터 결과 CSV', sec.to_csv(index=False).encode('utf-8-sig'),
                         f"sector_{m.key}_{res['asof']}.csv", 'text/csv', width='stretch',
                         key=f'{m.key}_dl_sec')
    stk = _stock_frame(m, res['stocks'], labels, names)
    c[1].download_button('종목 결과 CSV', stk.to_csv(index=False).encode('utf-8-sig'),
                         f"stocks_{m.key}_{res['asof']}.csv", 'text/csv', width='stretch',
                         key=f'{m.key}_dl_stk')


@st.cache_data(ttl=1800, show_spinner=False)
def _auto_phase(index_name: str, asof: str, idx_key: int) -> dict | None:
    rec = store.load_snapshot('indices').get('data', {}).get(index_name)
    if not rec:
        return None
    return nl.detect_phase(store._records_to_df(rec), asof)


def _rs_range(m: Market, asof: str) -> tuple[str, str]:
    """추세추종 탭과 같은 '⚙️ RS 구간 설정'. 비워두면 자동. 반환: (기산점, 찐반등일) 문자열, 자동이면 ''."""
    auto = _auto_phase(m.phase_index, asof, _mtime(store._snapshot_path('indices'))) or {}
    ap, aj = auto.get('autoPeakDate'), auto.get('autoJjinDate')
    max_d = pd.Timestamp(asof).date()
    with st.expander('⚙️ RS 구간 설정', expanded=False):
        c1, c2 = st.columns(2)
        c1.caption(f'자동 기산점: {ap:%Y-%m-%d}' if ap is not None else '자동 기산점: 없음 (조정 미감지)')
        peak = c1.date_input('기산점 수동 설정', value=None, max_value=max_d, key=f'{m.key}_rs_peak',
                             help='비워두면 지수가 21EMA를 이탈하기 전 고점을 씁니다. '
                                  '"지수 대비 (기산점 이후)"·덜 빠짐·저점 선행이 이 날짜부터 다시 계산됩니다.')
        c2.caption(f'자동 찐반등일: {aj:%Y-%m-%d}' if aj is not None else '자동 찐반등일: 없음 (미감지)')
        jjin = c2.date_input('찐반등일 수동 설정', value=None, max_value=max_d, key=f'{m.key}_rs_jjin',
                             help='비워두면 자동 감지한 찐반등일을 씁니다. 지정하면 찐반등 이후 화면으로 바뀌고 '
                                  '"찐반등 후 지수 대비"가 이 날짜부터 계산됩니다.')
        if peak and jjin and peak >= jjin:
            st.warning('기산점이 찐반등일보다 앞이어야 합니다 — 기산점 설정을 무시합니다.')
            peak = None
        st.caption('휴장일을 고르면 직전 거래일로 맞춥니다. 기준일이 바뀌어도 설정은 유지됩니다.')
    return (str(peak) if peak else '', str(jjin) if jjin else '')


def render_sector_leadership_tab(market_key: str = 'US'):
    m = MARKETS[market_key]
    if not _universe(m):
        st.info('와치리스트가 비어 있습니다. 사이드바에서 업로드해 주세요.')
        return
    opts = _asof_options(m)
    if not opts:
        st.warning(f"스냅샷에 {'·'.join(m.bench)} 일봉이 없습니다. 다음 배치 수집 후 표시됩니다.")
        return
    # 기준일 선택 상자는 '시장 현황' 접기 안에 있지만, 그 제목(국면)을 그리려면 기준일이 먼저 필요하다.
    # 위젯 값은 실행 시작 시 session_state에 이미 있으므로 거기서 읽는다.
    key_asof = f'{m.key}_asof'
    if st.session_state.get(key_asof) not in opts:
        st.session_state[key_asof] = opts[0]
    asof = st.session_state[key_asof]
    peak, jjin = _rs_range(m, asof)     # RS 구간 설정은 맨 위
    try:
        res = _compute(m.key, asof, _data_key(m), peak, jjin)
    except ValueError as e:
        st.error(f'계산할 수 없습니다: {e}')
        return
    labels, names = _labels(m), _names(m)

    with st.expander(_phase_title(m, res['phase']), expanded=True):
        st.caption(f"{m.title} · {'·'.join(m.bench)} 대비")
        strip, pick = st.container(border=True).columns([5, 1], vertical_alignment='center')
        pick.selectbox('기준일', opts, key=key_asof, label_visibility='collapsed',
                       format_func=lambda d: f'기준일 {d}',
                       help='과거 날짜를 고르면 그날 종가까지만 써서 다시 계산합니다')
        with strip:
            _market_strip(m, res)
        if res['phase'] is not None:
            _phase_banner(m, res)
    # 화면 배치는 국면과 상관없이 고정 — 시장 현황 → 차기 주도섹터 → 차기 주도주 → 상승 추세 확인(A/B) → 자세히 보기.
    # 국면에 따라 바뀌는 건 A/B 섹션이 기본으로 펼쳐져 있는지뿐이다.
    if res['phase'] is not None:
        with st.expander('**🧭 차기 주도섹터**', expanded=True):
            _lead_sectors(m, res, labels, names)
        with st.expander('**🎯 차기 주도주**', expanded=True):
            _lead_stocks(m, res, labels, names)
    uptrend = res['phase'] is None or res['phase']['mode'] == 'uptrend'
    with st.expander('**🔄 순환매 — 새 주도섹터 찾기 (최근 1주·1개월 RS · 10EMA)**', expanded=uptrend):
        if not uptrend:
            st.caption('찐반등 2주 뒤부터 주로 보는 섹션입니다. 조정 중에는 대부분 미달이 정상입니다.')
        records = cls.load_store(m.key)
        _rotation(res, labels, names, records)
        _next_sectors(res, labels, names, records)
        _next_stocks(m, res, labels, names)

    views = ['섹터 지도', '섹터별 종목', '전체 순위', '분류 수정', '계산 방법']
    with st.expander('자세히 보기 — ' + ' · '.join(views)):
        view = st.segmented_control('보기', views, default=views[0], key=f'{m.key}_detail_view',
                                    label_visibility='collapsed') or views[0]
        main = [s for s in res['sectors'] if s['inMainRanking']]
        if view == '섹터 지도':
            _heatmap(m, main, labels)
        elif view == '섹터별 종목':
            _drilldown(m, res, labels, names)
        elif view == '전체 순위':
            _leaderboards(m, res, labels, names)
        elif view == '분류 수정':
            _classification_editor(m, labels, names)
        else:
            _methodology(m, res)
            st.markdown('**메인 랭킹 밖 테마** (US: 바이오·금속·석유가스·소비, KR: 지주·기타)')
            _sector_table([s for s in res['sectors'] if not s['inMainRanking']], labels)
            _downloads(m, res, labels, names)
