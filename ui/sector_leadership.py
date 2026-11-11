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

from ui import tv_export

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
    'US': Market('US', '나스닥 기술·혁신 성장주 210종목', ('NASDAQ',), 'indices', (),
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
    # 점수 공식이 바뀌면 SCORE_VERSION을 올린다 — st.cache_data는 다른 모듈(next_leaders) 코드 변경을
    # 감지하지 못해, 옛 캐시(새 필드 없는 결과)를 새 화면 코드가 읽다 KeyError가 났다(2026-11-10 gap200).
    return (SCORE_VERSION,) + tuple(_mtime(p) for p in paths)


@st.cache_data(ttl=1800, show_spinner=False)
def _compute(market_key: str, asof: str, data_key: tuple, peak: str = '', jjin: str = '') -> dict:
    m = MARKETS[market_key]
    tickers = [t for t, _ in _universe(m)]
    data = store.load_snapshot(m.key).get('data', {})
    bars = {t: store._records_to_df(data[t]) for t in tickers if t in data}
    # 저변동(ADR 낮은) 종목은 아예 제외 — 추세추종 탭과 같은 기준(KR 2%, US 4%, 기준일 직전 20일).
    # 방어주가 RS/ADR(작은 ADR로 나눠 커짐)로 '덜 빠짐' 상위를 차지하는 것을 막는다.
    adr_min = 4.0 if m.key == 'US' else 2.0
    low_adr = []
    for t in list(bars):
        d = bars[t][bars[t].index <= pd.Timestamp(asof)].tail(20)
        if len(d) >= 10 and float(((d['High'] - d['Low']) / d['Close']).mean() * 100) < adr_min:
            low_adr.append(t)
            del bars[t]
    tickers = [t for t in tickers if t not in low_adr]
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
    earlier = []        # [(기준일, 결과)] 1주 전, 2주 전
    for back in (5, 10):
        if pos - back >= 0:
            try:
                d = bench_dates[pos - back]
                earlier.append((d, ss.run(bars, tickers, theme_of, main, d, tax['version'], **kw)))
            except ValueError:
                break
    ss.attach_trend(rep.sectors, [e.sectors for _, e in earlier])
    idx_rec = store.load_snapshot('indices').get('data', {}).get(m.phase_index)
    phase, picks, ranked, thin = None, [], [], []
    rot_stocks, rot_ranked, rot_thin = [], [], []
    if idx_rec:
        idx_df = store._records_to_df(idx_rec)
        phase = nl.detect_phase(idx_df, asof, peak or None, jjin or None)
        picks = nl.score_stocks(rep.stocks, bars, idx_df, theme_of, main, phase, asof)
        # 섹터 순위는 메인 테마만 ('기타 소비'·'지주·기타' 같은 잡다한 묶음은 섹터로 의미가 없어서). 종목 표에는 전부 나온다.
        in_main = lambda pool: [p for p in pool if p['primaryTheme'] in main]
        ranked, thin = nl.rank_sectors(in_main(picks))
        # 순환매 — 지금과 2주 전을 같은 방식으로 계산해 '새로' 올라온 섹터를 가린다
        rot_stocks = nl.rotation_scores(rep.stocks, bars, idx_df, theme_of, main, asof)
        prev = (nl.rotation_scores(earlier[1][1].stocks, bars, idx_df, theme_of, main, earlier[1][0])
                if len(earlier) > 1 else [])
        rot_ranked, rot_thin = nl.rotation_sectors(in_main(rot_stocks), in_main(prev))
    return {
        'asof': rep.asof,
        'bench': {'names': list(rep.bench.names), 'ret5': rep.bench.ret5, 'ret21': rep.bench.ret21,
                  'blend5': rep.bench.blend5, 'blend21': rep.bench.blend21},
        'stocks': rep.stocks, 'sectors': rep.sectors, 'leaders': rep.leaders,
        'coverage': rep.coverage, 'taxonomyVersion': tax['version'],
        'phase': phase, 'picks': picks, 'nextSectors': ranked, 'thinSectors': thin,
        'rotStocks': rot_stocks, 'rotSectors': rot_ranked, 'rotThin': rot_thin,
        'lowAdr': sorted(low_adr), 'adrMin': adr_min,
        'hot': nl.hot_sectors(bars, theme_of, asof), 'themeOf': dict(theme_of),
        'bullSince': nl.bull_since({t: bars[t] for t in theme_of if t in bars}, asof),
    }


SCORE_VERSION = 2026_11_11_5     # next_leaders 점수·필드가 바뀌면 올릴 것 (캐시 무효화)


def hot_history(market_key: str) -> tuple[dict, dict, dict]:
    """(🔥 섹터 {테마: 정보}, {티커: 테마}, {테마: 라벨}) — 최신 기준일. 10EMA 탭 배지용 단일 출처."""
    m = MARKETS[market_key]
    opts = _asof_options(m)
    if not opts:
        return {}, {}, {}
    try:
        res = _compute(m.key, opts[0], _data_key(m))
    except ValueError:
        return {}, {}, {}
    return res.get('hot') or {}, res.get('themeOf') or {}, _labels(m)


def hot_badge(info: dict, label: str) -> str:
    """'🔥 광통신 (지금)' / '🔥 광통신 10/20'."""
    when = ('지금' if info['now'] else '6주 전부터' if info['last'] is None else f"{info['last']:%m/%d}")
    return f"🔥 {label} ({when})"


def lead_scores(market_key: str, peak: str = '', jjin: str = '') -> dict:
    """차기 주도주 점수를 다른 탭(추세추종)에서 그대로 쓰기 위한 단일 출처.
    {ticker: {'score', 'rank', 'parts', 'asof', 'phaseLabel'}} — 순위는 메인 밖 테마 포함 전체 기준.
    계산은 주도섹터 탭과 같은 _compute(같은 캐시)를 쓰므로 두 탭 점수가 어긋날 수 없다."""
    m = MARKETS[market_key]
    opts = _asof_options(m)
    if not opts:
        return {}
    try:
        res = _compute(m.key, opts[0], _data_key(m), peak, jjin)
    except ValueError:
        return {}
    picks = sorted(res['picks'], key=lambda p: -p['nextScore'])
    ph = res.get('phase') or {}
    label = ph.get('label', '')
    has_jjin = ph.get('jjinDate') is not None     # 찐반등 전엔 상승국면 값 없음 — 주도섹터 탭과 같게
    return {p['ticker']: {'score': p['nextScore'], 'rank': i, 'parts': p.get('nextParts', {}),
                          'asof': res['asof'], 'phaseLabel': label,
                          'downAdr': p.get('sincePeakExcessAdr'),
                          'upAdr': p.get('sinceJjinExcessAdr') if has_jjin else None}
            for i, p in enumerate(picks, 1)}


def _asof_options(m: Market) -> list:
    data = store.load_snapshot(m.bench_snapshot).get('data', {})
    recs = [data.get(b) for b in m.bench]
    if not all(recs):
        return []
    common = set(recs[0]['dates'])
    for r in recs[1:]:
        common &= set(r['dates'])
    # 지수(indices 스냅샷, KR 배치)가 종목 스냅샷보다 하루 앞설 수 있다 — 예: 2026-11-10 NASDAQ은 11/09까지,
    # US 종목은 yfinance 지연으로 11/06까지 → 기준일 11/09에 전 종목 '기준일 종가 없음'(0/206).
    # 종목 데이터가 있는 마지막 날까지만 기준일로 내준다.
    stocks = store.load_snapshot(m.key).get('data', {})
    last = max((v['dates'][-1] for k, v in stocks.items() if k not in m.bench and v.get('dates')), default=None)
    if last:
        common = {d for d in common if d <= last}
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
  padding:8px 14px;border-bottom:1px solid rgba(128,128,128,.25);white-space:nowrap}
.sl-t td{padding:11px 14px;border-bottom:1px solid rgba(128,128,128,.12);vertical-align:middle;white-space:nowrap}
.sl-t tbody tr:hover td{background:rgba(128,128,128,.05)}
/* 메인 네 표(차기 주도섹터·차기 주도주·급부상 섹터·급부상 종목) 공통 — 열 폭 고정, 긴 머리글·설명은 줄바꿈 */
.sl-t.fx{table-layout:fixed}
.sl-t.fx th{white-space:normal;line-height:1.3;vertical-align:middle}
.sl-t.fx td{white-space:normal;line-height:1.45}
.sl-t td.rk{opacity:.5;font-variant-numeric:tabular-nums}
.sl-t .two{font-size:.74rem;opacity:.55;font-weight:400;margin-top:2px}
.hb-num{font-variant-numeric:tabular-nums;white-space:nowrap}
.hb-bar{position:relative;height:4px;width:100%;max-width:110px;margin-top:6px;border-radius:2px;
  background:rgba(128,128,128,.16);overflow:hidden}
.hb-bar i{position:absolute;left:0;top:0;bottom:0;border-radius:2px}
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
# 표 안에서 쓰는 짧은 배지 — 긴 문구는 좁은 열에서 두 줄로 깨져 읽기 어렵다 (뜻은 마우스를 올리면)
_IMPROVING_SHORT = ('<span class="sl-badge sl-A" style="white-space:nowrap" '
                    'title="1주 전보다 10EMA 위 비율 +10%p 이상, 5일 RS/ADR 상승, 거래량 확산 유지가 동시에 성립">📈 동시 개선</span>')


def _th_two(label: str) -> str:
    """'하락국면 RS/ADR (기산점 10/06 ~ 찐반등 11/10)' → 위 줄 이름, 아래 줄 작게 기간 — 머리글이 어중간하게 끊기지 않게."""
    if ' (' in label and label.endswith(')'):
        head, rest = label.split(' (', 1)
        return f'{head}<div class="two" style="opacity:.9">({rest[:-1]})</div>'
    return label


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
    if res.get('lowAdr'):
        parts.append(box(f'ADR {res["adrMin"]:.0f}% 미만 제외', f'{len(res["lowAdr"])}종목'))
    st.markdown(_CSS + '<div class="sl-mkt">' + ''.join(parts) + '</div>', unsafe_allow_html=True)
    if res.get('phase') is None and b['blend5'] < 0 and b['blend21'] < 0:   # 국면 안내가 있으면 중복
        st.caption(f"⚠️ 시장({'·'.join(b['names'])}{' 평균' if len(b['names']) > 1 else ''})이 1주·1개월 모두 하락 중 — 주도섹터 확정이 어렵고 포지션 규모를 줄이는 구간입니다.")


_NEW_BADGE = '<span class="sl-badge sl-A">🆕 새 순환매</span>'


def _rot_badge(s: dict) -> str:
    """🆕 새 순환매 / 기존 강세(2주 전에도 50↑, 지금 60↑) / ↗ 올라오는 중(+15↑, 아직 60 미만) / 없음."""
    if s['isNew']:
        return _NEW_BADGE
    prev, rise = s.get('prevScore'), s.get('scoreRise')
    if prev is not None and prev >= nl.ROT_WAS_WEAK and s['score'] >= nl.ROT_NOW:
        return '<span class="sl-badge sl-C">기존 강세</span>'
    if rise is not None and rise >= nl.ROT_RISE:
        return '<span class="sl-badge sl-B">↗ 올라오는 중</span>'
    return ''


def _rot_change(s: dict) -> str:
    if s.get('prevScore') is None:
        return '—'
    return (f'<span class="sl-sub" style="margin:0">{s["prevScore"]:.0f}</span> → <b>{s["score"]:.0f}</b> '
            f'{_signed(s["scoreRise"], "", 0)}')


def _bar_cell(v: float, scale: float = 100, color: str = '#16a34a', text: str | None = None, sub: str = '') -> str:
    """숫자 + 가는 막대 — 메인 네 표의 점수·비율 칸 공통 모양. v는 0~scale."""
    w = 0 if v is None else max(0.0, min(1.0, v / scale)) * 100
    return (f'<div class="hb-num"><b>{text if text is not None else f"{v:.0f}"}</b>{sub}</div>'
            f'<div class="hb-bar"><i style="width:{w:.0f}%;background:{color}"></i></div>')


def _colgroup(widths: list) -> str:
    return '<colgroup>' + ''.join(f'<col style="width:{w}%">' for w in widths) + '</colgroup>'


def _stock_title(t: str, nm: str) -> str:
    """종목 칸 — 이름 굵게, 아래 줄에 티커."""
    return (f'<div class="sl-name">{_esc(nm)}</div><div class="two">{_esc(t)}</div>' if nm != t
            else f'<div class="sl-name">{_esc(t)}</div>')


def _hot_block(res: dict, labels: dict, names: dict, records: dict):
    """🔥 급부상 섹터 — 지금 🔥 + 최근 약 6주 안에 🔥였던 섹터 (이력)."""
    hot = res.get('hot') or {}
    if not hot:
        st.caption('최근 6주 안에 🔥였던 섹터가 없습니다.')
        return
    # 지금 🔥 먼저, 그 안에서 정배열 종목 비율 높은 순 (검증: 정배열 비율 높을수록 이후 기준봉이 더 자주 나옴)
    items = sorted(hot.items(), key=lambda kv: (not kv[1]['now'], -kv[1].get('alignedShare', 0)))
    def pct_bar(v, color, sub=''):
        return _bar_cell(v, 1, color, f'{100 * v:.0f}%', sub)

    rows = []
    for th, h in items:
        state = ('<span class="sl-badge sl-A">지금 🔥</span>' if h['now']
                 else f'<span class="sl-badge sl-C">꺼짐 · {h["daysAgo"]}거래일 전 시작</span>' if h['last'] is not None
                 else '<span class="sl-badge sl-C">꺼짐 · 6주보다 전에 시작</span>')
        fires = ', '.join(f'{d:%m/%d}' for d in h['fires']) or '6주보다 전'
        who = ', '.join(_esc(_stock_name(t, names, records)) for t in h['tickers'][:6])
        peak = '<span class="two" style="display:inline"> · 최고 %.0f%%</span>' % (100 * h['peakShare'])
        rows.append(f'<tr><td><div class="sl-name">{_esc(labels.get(th, th))}</div><div class="two">{h["n"]}종목</div></td>'
                    f'<td>{state}</td><td class="hb-num" style="white-space:normal">{fires}</td>'
                    f'<td>{pct_bar(h.get("alignedShare", 0), "#2563eb")}</td>'
                    f'<td>{pct_bar(h["share"], "#f97316", peak)}</td>'
                    f'<td style="font-size:.84rem">{who or "—"}</td></tr>')
    st.markdown('<div class="sl-wrap"><table class="sl-t fx" style="min-width:820px">'
                + _colgroup([15, 17, 10, 13, 15, 30]) +
                '<thead><tr><th>섹터</th><th>상태</th><th>🔥 시작일</th>'
                '<th>정배열 종목 비율</th><th>현재 렐볼 양봉 비율</th><th>최근 5일 렐볼 양봉 종목</th></tr></thead><tbody>'
                + ''.join(rows) + '</tbody></table></div>', unsafe_allow_html=True)
    st.caption(f'🔥 = 섹터 종목({nl.MIN_SECTOR_N}개 이상)의 {100 * nl.HOT_SHARE:.0f}% 이상이 최근 5일 안에 거래량 '
               f'{nl.HOT_RVOL}배↑ 양봉. {nl.HOT_GAP}거래일 안에 다시 켜진 건 같은 🔥로 보고, 약 6주({nl.HOT_LOOKBACK}거래일) 이력을 남깁니다.  \n'
               f'단, 섹터의 정배열(10EMA>21EMA>50SMA) 종목 비율이 {100 * nl.HOT_MIN_ALIGNED:.0f}% 미만이면 🔥여도 급부상 섹터로 보지 않습니다.')
    st.markdown('<div style="margin-top:10px;font-weight:700;font-size:.9rem">상태 설명</div>'
                '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>상태</th><th>뜻</th></tr></thead><tbody>'
                '<tr><td><span class="sl-badge sl-A">지금 🔥</span></td><td>오늘도 돈이 몰리는 중</td></tr>'
                '<tr><td><span class="sl-badge sl-C">꺼짐 · N거래일 전 시작</span></td>'
                '<td>N거래일 전에 🔥가 켜졌다가 지금은 꺼짐 — 돈이 몰린 뒤 쉬는 중, PP 기준봉을 기다릴 구간</td></tr>'
                '<tr><td><span class="sl-badge sl-C">꺼짐 · 6주보다 전에 시작</span></td>'
                '<td>6주 창이 시작될 때 이미 🔥여서 시작일을 알 수 없고, 지금은 꺼짐</td></tr>'
                '</tbody></table></div>', unsafe_allow_html=True)


def _rotation_sectors(m: Market, res: dict, labels: dict, names: dict, records: dict):
    """순환매 점수 섹터 순위 — 2026-11-11부터 '자세히 보기'로 이동 (급부상 감지는 🔥가 더 빠르고 정확)."""
    ranked, thin = res.get('rotSectors') or [], res.get('rotThin') or []
    in_trend = _trend_tickers(m)
    st.markdown('<div class="sl-note">찐반등이 아니라 <b>최근 1~2주</b> 기준으로, 지금 돈이 <b>새로</b> 들어오는 섹터를 찾습니다. '
                '점수 배점은 아래 순환매 종목 표 밑에 있습니다. 섹터 점수는 차기 주도섹터와 같은 방식'
                '(중앙값 60% + 70점↑ 비율 40%, 3종목 이상). '
                f'<b>🆕 새 순환매</b> = 2주 전 {nl.ROT_WAS_WEAK}점 미만 → 지금 {nl.ROT_NOW}점 이상, +{nl.ROT_RISE} 이상.</div>',
                unsafe_allow_html=True)
    if not ranked:
        st.caption('3종목 이상인 섹터가 없어 순위를 매길 수 없습니다.')
        return
    trend = {s['theme']: s for s in res['sectors']}

    def stock_list(s, n=3):
        return ', '.join(
            f"{_esc(_stock_name(r['ticker'], names, records))} {r['rotScore']:.0f}"
            + ('' if r['ticker'] in in_trend else ' <span class="sl-off">(추세추종 탭에 없음)</span>')
            for r in s['top'][:n])

    cards = []
    for i, s in enumerate(ranked[:3]):
        t = trend.get(s['theme'], {})
        lines = [('점수 (2주 전 → 지금)', _rot_change(s)),
                 ('52주 고점 대비 (중앙값)', _signed(s['medianOffHigh'], '%')),
                 ('10EMA 위 종목 비율 (2주전→1주전→기준일)', _trend_txt(t.get('history'))),
                 ('1주 RS/ADR (중앙값)', _signed(s['medianRotRs5'], '', 2)),
                 ('양봉 렐볼 나온 종목 비율 (최근 5일)', f"{100 * s['pctBullRvol']:.0f}%"),
                 ('강한 종목 (70점↑)', f"{round(s['strongShare'] * s['n'])}/{s['n']}")]
        body = ''.join(f'<div class="nl-line"><span>{a}</span><span>{b}</span></div>' for a, b in lines)
        badge = f'<div class="nl-badge-row">{_rot_badge(s)}</div>'
        cards.append(f'<div class="nl-card {"top" if s["isNew"] else ""}"><div class="nl-head"><div>'
                     f'<div class="nl-rank">{i + 1}위</div><div class="nl-title">{_esc(labels[s["theme"]])}</div></div>'
                     f'<div class="nl-score">{s["score"]:.0f}</div></div>'
                     f'{badge}{body}<div class="nl-stocks"><span style="opacity:.6">대표 </span>{stock_list(s)}</div></div>')
    st.markdown(_CARD_CSS + '<div class="nl-cards">' + ''.join(cards) + '</div>', unsafe_allow_html=True)

    rest = ranked[3:]
    if rest:
        rows = ''.join(
            f'<tr><td>{i + 4}</td><td><span class="sl-name">{_esc(labels[s["theme"]])}</span>'
            f'<span class="sl-sub">{s["n"]}종목</span>'
            + (f'<div style="margin-top:3px">{_rot_badge(s)}</div>' if _rot_badge(s) else '') + '</td>'
            f'<td>{_rot_change(s)}</td><td>{_signed(s["medianOffHigh"], "%")}</td>'
            f'<td>{_trend_txt(trend.get(s["theme"], {}).get("history"))}</td>'
            f'<td>{_signed(s["medianRotRs5"], "", 2)}</td><td>{100 * s["pctBullRvol"]:.0f}%</td>'
            f'<td class="sl-need">{stock_list(s)}</td></tr>'
            for i, s in enumerate(rest))
        st.markdown(
            '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>순위</th><th>섹터</th>'
            '<th>점수 (2주 전 → 지금)</th><th>52주 고점 대비 (중앙값)</th>'
            '<th>10EMA 위 종목 비율 (2주전→1주전→기준일)</th><th>1주 RS/ADR</th><th>양봉 렐볼 비율</th>'
            '<th>대표주</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>', unsafe_allow_html=True)
    if thin:
        st.caption('표본 부족(3종목 미만)으로 순위 제외: '
                   + ', '.join(f"{labels[s['theme']]}({s['n']})" for s in thin))


def _rotation_stocks(m: Market, res: dict, labels: dict, names: dict, records: dict):
    # 🔥 급부상 섹터(지금 또는 최근 6주) 소속 종목만 — 정렬은 PP 가능성 점수
    tv_export.register_hot(m.key, [])       # 조기 return 때 직전 목록이 남지 않게 먼저 비운다
    hot = res.get('hot') or {}
    # 섹터 종목이 3개 미만이라 🔥 판정을 못 받는 종목 — 최근 5일 렐볼 양봉이면 '단독 급등 종목'으로 함께 보여준다
    size: dict = {}
    for th in (res.get('themeOf') or {}).values():
        size[th] = size.get(th, 0) + 1
    solo = {r['ticker'] for r in res.get('rotStocks') or []
            if size.get(r['primaryTheme'], 0) < nl.MIN_SECTOR_N and r['hasBullishRvol15InLast5']}
    pool = [r for r in res.get('rotStocks') or [] if r['primaryTheme'] in hot or r['ticker'] in solo]
    # ADR 6% 미만은 10EMA 탭 판정 대상이 아니라 아예 뺀다
    pool = [r for r in pool if r['ppEligible']]
    # 탈락 — 편입 후 종가 고점에서 3 ADR 이상 밀린 종목 (5년 검증: 이후 30일 기준봉 3.6~3.9% vs 평균 16~17%)
    asof_ts = pd.Timestamp(res['asof'])
    bull = res.get('bullSince') or {}
    data = store.load_snapshot(m.key).get('data', {})

    def since_entry(r):
        """(편입 후 종가 고점 대비 하락 ADR 배수, 편입 후 자기 렐볼 양봉 있었나).
        🔥는 최근 5일 렐볼 양봉으로 켜지므로 종목의 움직임은 편입일보다 최대 5거래일 앞설 수 있다 — 그날부터 잰다."""
        h = hot.get(r['primaryTheme'])
        since = h['since'] if h else bull.get(r['ticker'])
        rec = data.get(r['ticker'])
        if since is None or not rec or not r['adrNow']:
            return 0.0, bool(r['hasBullishRvol15InLast5'])
        df = store._records_to_df(rec)
        df = df[df.index <= asof_ts]
        avgv = df['Volume'].shift(1).rolling(20).mean()
        own = (df['Close'] > df['Open']) & (df['Volume'] >= nl.HOT_RVOL * avgv)
        pos = df.index.searchsorted(since)
        w = df.iloc[max(0, pos - 5):]
        if w.empty:
            return 0.0, bool(r['hasBullishRvol15InLast5'])
        dd = 100 * (float(w['Close'].iloc[-1]) / float(w['Close'].max()) - 1) / r['adrNow']
        return dd, bool(own.iloc[max(0, pos - 5):].any())

    for r in pool:
        r['ddAdr'], r['ownBull'] = since_entry(r)
    # 종목 자신도 핫해야 한다 — 편입 후 자기 렐볼 양봉이 한 번도 없으면 섹터만 뜨겁고 종목은 안 움직인 것.
    # 검증(🔥 뒤 0·10·20일): 없음 4~36%, 이후 30일 기준봉 17~21% vs 있음 21~25%
    pool = [r for r in pool if r['ownBull']]
    # 탈락 2 — 종가가 50일선 아래 (검증: 3 ADR에 안 걸린 종목 중 이후 30일 기준봉 8.6~9.8% vs 평균 16~17%).
    # 두 규칙 모두 매일 다시 계산 — 회복하면 다음 날 목록에 돌아온다
    # 탈락 3 — 종가가 200일선 아래 (PP 기준봉 조건이 150·200일선 위라 '50점 미만도 보기'에서도 뺀다)
    pool = [r for r in pool if r['ddAdr'] > -nl.PP_DROP_ADR and not r['belowSma50']
            and not (r.get('gap200') is not None and r['gap200'] < 0)]
    if not pool:
        st.caption('최근 6주 안에 🔥 급부상 섹터도, 단독 급등 종목도 없습니다.')
        return
    in_trend = _trend_tickers(m)
    bar = st.container(horizontal=True, gap='medium')      # 차기 주도주 토글과 같은 배치
    only_trend = bar.toggle('추세추종 탭 종목만', key=f'{m.key}_rot_only_trend', width='content')
    show_weak = bar.toggle(f'{nl.PP_MIN_SCORE}점 미만도 보기', key=f'{m.key}_rot_show_weak', width='content',
                           help=f'PP 가능성 점수 {nl.PP_MIN_SCORE}점 미만 종목도 표에 넣습니다 (기본은 숨김)')
    if not show_weak:
        pool = [r for r in pool if r['ppScore'] >= nl.PP_MIN_SCORE]
    hidden = _theme_toggles(m, bar, labels, {r['primaryTheme'] for r in pool}, key_prefix=f'{m.key}_rot_show')
    rows = sorted((r for r in pool
                   if (not only_trend or r['ticker'] in in_trend)
                   and r['primaryTheme'] not in hidden),
                  key=lambda r: -r['ppScore'])[:30]
    tv_export.register_hot(m.key, [r['ticker'] for r in rows])   # 화면 표 그대로
    if not rows:
        st.caption('조건에 맞는 종목이 없습니다.')
    asof = pd.Timestamp(res['asof'])
    bull = res.get('bullSince') or {}

    def entry(r):
        """(편입일, 날짜 모름) — 🔥 섹터면 섹터가 처음 켜진 날, 단독 렐볼이면 최근 5일 첫 렐볼 양봉 날."""
        h = hot.get(r['primaryTheme'])
        if h:
            return h['since'], h['startedBefore']
        return bull.get(r['ticker']), False

    body = []
    for rank, r in enumerate(rows, 1):
        t = r['ticker']
        nm = _stock_name(t, names, records)
        title = _stock_title(t, nm)
        d, unknown = entry(r)
        is_new = d is not None and not unknown and d == asof
        if is_new:
            title = '<span class="sl-badge sl-A" style="margin-bottom:3px">NEW</span>' + title
        if t not in in_trend:
            title += '<div class="sl-off">추세추종 탭에 없음</div>'
        if t in solo:
            title += '<div class="sl-off">섹터 종목 부족 · 단독 렐볼</div>'
        h = hot.get(r['primaryTheme'])
        star = f' <span class="two" style="display:inline">🔥{" 지금" if h["now"] else ""}</span>' if h else ''
        dots = '●' * r['ema10DaysOfLast5'] + '○' * (5 - r['ema10DaysOfLast5'])
        body.append(
            f'<tr><td class="rk">{rank}</td><td>{title}</td>'
            f'<td>{_esc(labels.get(r["primaryTheme"], ""))}{star}'
            f'<div class="two">{_esc((records.get(t) or {}).get("detail", ""))}</div></td>'
            f'<td class="hb-num">{_signed(r["offHigh"], "%")}</td>'
            f'<td><span class="{"sl-pos" if r["aboveEma10"] else "sl-neg"}">{dots}</span></td>'
            f'<td>{"✓" if r["aligned"] else ""}</td>'
            f'<td class="hb-num">{_signed(r["rotRs5"], "", 2)}</td>'
            f'<td>{"✓" if r["hasBullishRvol15InLast5"] else ""}</td>'
            f'<td>{_bar_cell(r["ppScore"])}</td></tr>')
    if rows:
        st.markdown(
            '<div class="sl-wrap"><table class="sl-t fx" style="min-width:900px">' + _colgroup([5, 17, 21, 9, 10, 7, 9, 9, 13])
            + '<thead><tr><th>순위</th><th>종목</th><th>섹터 · 테마</th>'
            '<th>52주 고점 대비</th><th>10EMA 최근 5일</th><th>정배열</th><th>1주 RS/ADR</th>'
            '<th>양봉 렐볼 (최근 5일)</th><th>PP 가능성 점수</th></tr></thead><tbody>' + ''.join(body)
            + '</tbody></table></div>', unsafe_allow_html=True)
        st.caption('🔥 지금 = 오늘도 급부상 중. 매수 신호가 아닙니다 — 10EMA PP 기준봉·셋업이 나올 때까지 관찰.  \n'
                   f'섹터 종목 부족 · 단독 렐볼 = 섹터 분류는 됐지만 같은 섹터 종목이 {nl.MIN_SECTOR_N}개 미만이라 '
                   '섹터 🔥 판정이 안 되는데, 최근 5일 안에 렐볼 양봉이 나온 종목.')
    _pp_score_table()


_PP_ITEMS = [
    ('고점 근접', '52주 고점 근접', '52주 최고가 대비 −20% 이내면 만점, 밖이면 0'),
    ('10EMA 유지', '10EMA 유지', '최근 5거래일 중 종가가 10EMA 위였던 날 수 ÷ 5'),
    ('정배열', '정배열 (10EMA > 21EMA > 50SMA)', '맞으면 만점 — 10EMA 탭 기준봉 조건'),
    ('양봉 렐볼', '양봉 렐볼', '최근 5일 안에 거래량 1.5배↑ 양봉이 있으면 만점'),
    ('1주 RS/ADR', '1주 RS/ADR', '최근 5거래일 지수 대비 초과수익 ÷ ADR — 와치리스트 안 상대 순위'),
]


def _pp_score_table():
    w = nl.PP_WEIGHTS
    rows = ''.join(
        f'<tr><td><span class="sl-name">{name}</span><div class="sl-sub" style="margin:0">{desc}</div></td>'
        f'<td><b>{w.get(k, 0)}</b></td></tr>' for k, name, desc in _PP_ITEMS)
    st.markdown(
        '<div style="margin-top:10px;font-weight:700;font-size:.9rem">PP 가능성 배점 (100점) — 기준봉을 만들어낼 가능성</div>'
        '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>항목</th><th>배점</th></tr></thead>'
        f'<tbody>{rows}<tr><td><b>합계</b></td><td><b>{sum(w.values())}</b></td></tr></tbody></table></div>',
        unsafe_allow_html=True)



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
    groups = {
        '차기 주도주 후보': ('1주 RS가 플러스로 돌아서고 10EMA 위로 올라왔지만, 1개월 RS나 50일선은 아직 회복 전인 종목',
                       [r for r in res['leaders'] if r['leaderType'] in ('EMERGING', 'REBOUND')]),
        '이미 강한 주도주': ('1주·1개월 RS 모두 플러스이고 10·21EMA·50SMA 위에 있는 종목',
                       [r for r in res['leaders'] if r['leaderType'] == 'LEADER_TREND']),
    }
    pick = st.segmented_control('종목 구분', list(groups), default='차기 주도주 후보',
                                key=f'{m.key}_stock_group', label_visibility='collapsed') or '차기 주도주 후보'
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
.nl-phase.rebound.late{background:rgba(37,99,235,.08);border-left:4px solid #2563eb}
.nl-phase.uptrend{background:rgba(22,163,74,.08);border-left:4px solid #16a34a}
.nl-view{font-size:.8rem;opacity:.8;margin:-6px 0 14px;padding:8px 12px;border-radius:8px;
  background:rgba(128,128,128,.08)}
.nl-score-bar{display:inline-block;vertical-align:middle;height:6px;border-radius:3px;background:#16a34a;margin-left:6px}
</style>"""

_DEFENSIVE = {'FINANCE', 'HOLDING_OTHER', 'COSMETICS_CONSUMER', 'TELECOM_NETWORK', 'CONSUMER_OTHER'}
_STAR = '★'


def _rebound_ball(label: str) -> str:
    """찐반등 국면 공 색 — 추세추종 탭과 같게: DAY2 🟡, DAY3~7 🔵."""
    return '🟡' if label == 'DAY2' else '🔵'


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
        body = (f"<b>{_rebound_ball(ph['label'])} 찐반등 {ph['label']}{manual}</b> — 기산점 {ph['peakDate']:%m/%d} · 찐반등일 {ph['jjinDate']:%m/%d} 이후 {idx} <b>{sj:+.1f}%</b>.<br>"
                "조정 중 버틴 종목 가운데 <b>찐반등 이후 지수보다 먼저 치고 나가는</b> 종목이 진짜 주도주입니다. "
                "회복 속도에 가장 큰 비중을 둡니다.")
    else:
        last = (f" — 마지막 찐반등 {ph['jjinDate']:%m/%d} 이후 {idx} <b>{ph['indexSinceJjin']:+.1f}%</b>"
                if ph.get('jjinDate') is not None and ph.get('indexSinceJjin') is not None else '')
        body = (f"<b>🟢 상승 추세</b>{last}.<br>차기 주도섹터·주도주는 다음 조정(21EMA 이탈) 전까지 마지막 기산점·찐반등 기준을 "
                "그대로 이어 갑니다. 갑자기 돈이 몰리는 섹터는 아래 <b>'🔥 급부상 섹터'</b>에서 봅니다.")
    late = ' late' if ph['mode'] == 'rebound' and _rebound_ball(ph['label']) == '🔵' else ''
    st.markdown(_CARD_CSS + f'<div class="nl-phase {ph["mode"]}{late}">{body}</div>', unsafe_allow_html=True)


def _phase_title(m: Market, ph: dict | None) -> str:
    """지수 현황 접기 제목 — 접어 둬도 국면이 보이게."""
    if ph is None:
        return '**📊 지수 현황**'
    tag = {'correction': f"🔴 조정 중 ({ph['label']})", 'rebound': f"{_rebound_ball(ph['label'])} 찐반등 {ph['label']}"}.get(ph['mode'], '🟢 상승 추세')
    return f"**📊 지수 현황 · {m.phase_index} {tag}{' · 수동 설정' if ph.get('manual') else ''}**"


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
                 ('52주 고점 대비 (중앙값)', _signed(s['medianOffHigh'], '%')),
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
            f'<tr><td class="rk">{i + 4}</td><td><div class="sl-name">{_esc(labels[s["theme"]])}</div>'
            f'<div class="two">{s["n"]}종목</div></td><td>{_bar_cell(s["score"])}</td>'
            + ''.join(f'<td class="hb-num">{_rs_val(c, s, True)}</td>' for c in cols)
            + f'<td class="hb-num">{_signed(s["medianOffHigh"], "%")}</td>'
            f'<td>{_trend_txt(trend.get(s["theme"], {}).get("history"))}'
            + (f'<div style="margin-top:4px">{_IMPROVING_SHORT}</div>' if trend.get(s['theme'], {}).get('improving') else '') + '</td>'
            f'<td>{_trend_txt(trend.get(s["theme"], {}).get("history"), "pctAbove21")}</td>'
            f'<td style="font-size:.84rem">{", ".join(_esc(_stock_name(r["ticker"], names, records)) for r in s["top"])}</td></tr>'
            for i, s in enumerate(rest))
        st.markdown(
            '<div class="sl-wrap"><table class="sl-t fx" style="min-width:980px">'
            + _colgroup([5, 13, 10] + [9] * len(cols) + [9, 13, 13, 19])
            + '<thead><tr><th>순위</th><th>섹터</th><th>점수</th>'
            + ''.join(f'<th>{_th_two(c[0])}</th>' for c in cols) + '<th>' + _th_two('52주 고점 대비 (중앙값)') + '</th><th>' + _th_two('10EMA 위 종목 비율 (2주전→1주전→기준일)') + '</th><th>' + _th_two('21EMA 위 종목 비율 (2주전→1주전→기준일)') + '</th><th>대표주</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>', unsafe_allow_html=True)
    if thin:
        st.caption('표본 부족(3종목 미만)으로 순위 제외: '
                   + ', '.join(f"{labels[s['theme']]}({s['n']})" for s in thin))


_THEME_TOGGLES = {'KR_KOSPI': ('BIO_PHARMA', 'FINANCE'), 'KR_KOSDAQ': ('BIO_PHARMA', 'FINANCE')}


def _theme_toggles(m: Market, bar, labels: dict, present: set, key_prefix: str) -> set:
    """테마별 켜고 끄는 토글 — 차기 주도주·순환매 종목 표 공통. 끈(숨길) 테마 코드 집합을 돌려준다.
    국장 바이오·제약·금융 + 메인 밖 테마(KR 지주·기타, US 바이오·헬스케어·금속·소재 등), 모두 기본 꺼짐.
    두 표가 같은 토글 목록을 보이도록 표에 그 테마 종목이 없어도 항상 보여준다 (present는 쓰지 않음)."""
    side = list(_THEME_TOGGLES.get(m.key, ()))
    side += [t['code'] for t in cls.load_taxonomy(m.key)['themes']
             if not t['main'] and t['code'] != 'UNCLASSIFIED']
    return {code for code in side
            if not bar.toggle(labels.get(code, code), value=False, key=f'{key_prefix}_{code}', width='content',
                              help='켜면 이 테마 종목도 표에 넣습니다 (기본은 빠져 있음)')}


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
    bar = st.container(horizontal=True, gap='medium')     # 토글을 옆으로 바짝 붙여 배치
    only_top = bar.toggle('상위 3개 섹터 종목만', key=f'{m.key}_only_top', width='content')
    only_trend = bar.toggle('추세추종 탭 종목만', key=f'{m.key}_only_trend', width='content')
    hidden = _theme_toggles(m, bar, labels, {r['primaryTheme'] for r in picks}, key_prefix=f'{m.key}_show')
    rows = sorted((r for r in picks
                   if (not only_top or r['primaryTheme'] in top_sectors)
                   and (not only_trend or r['ticker'] in in_trend)
                   and r['primaryTheme'] not in hidden),
                  key=lambda r: -r['nextScore'])[:20]
    tv_export.register_lead(m.key, [r['ticker'] for r in rows])     # 화면 표 그대로(토글 반영, 상위 20)
    if not rows:
        st.caption('조건에 맞는 종목이 없습니다.')
        _score_table(rebound)
        return
    body = []
    for rank, r in enumerate(rows, 1):
        t = r['ticker']
        nm = _stock_name(t, names, records)
        title = _stock_title(t, nm)
        if t not in in_trend:
            title += '<div class="sl-off">추세추종 탭에 없음</div>'
        star = f' <span style="color:#16a34a">{_STAR}</span>' if r['primaryTheme'] in top_sectors else ''
        detail = (records.get(t) or {}).get('detail', '')
        lead = r.get('leadDays')
        lead_txt = '—' if lead is None else (f'{lead}일 먼저' if lead > 0 else '같이' if lead == 0 else f'{-lead}일 늦게')
        body.append(
            f'<tr><td class="rk">{rank}</td><td>{title}</td>'
            f'<td>{_esc(labels.get(r["primaryTheme"], ""))}{star}<div class="two">{_esc(detail)}</div></td>'
            + ''.join(f'<td class="hb-num">{_rs_val(c, r, False)}</td>' for c in cols)
            + f'<td class="hb-num">{_signed(r.get("offHigh"), "%")}</td><td class="hb-num">{_signed(r.get("gap200"), "%")}</td>'
            f'<td>{lead_txt}</td>'
            + f'<td>{"✓" if r.get("hasBullishRvol15InLast5") else ""}</td>'
            f'<td>{_bar_cell(r["nextScore"])}</td></tr>')
    st.markdown(
        '<div class="sl-wrap"><table class="sl-t fx" style="min-width:980px">'
        + _colgroup([5, 15, 18] + [9] * len(cols) + [8, 8, 8, 8, 12])
        + '<thead><tr><th>순위</th><th>종목</th><th>섹터 · 테마</th>'
        + ''.join(f'<th>{_th_two(c[0])}</th>' for c in cols) + '<th>52주 고점 대비</th><th>200일선 이격</th><th>저점</th>'
        '<th>양봉 렐볼 (최근 5일)</th><th>점수</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>',
        unsafe_allow_html=True)
    st.caption(f'{_STAR} = 상위 3개 섹터 소속. 매수 신호가 아닙니다 — 찐반등 확인 후 5분봉 렐볼 기준봉에서 진입.')
    _score_table(rebound)


_SCORE_ITEMS = [
    # (배점 키, 항목, 설명)
    ('덜 빠짐', '하락국면 RS/ADR', '기산점 ~ 찐반등(찐반등 전엔 ~ 기준일) 지수 대비 초과수익 ÷ ADR'),
    ('찐반등 후 회복', '상승국면 RS/ADR', '찐반등 전날 종가 ~ 기준일 지수 대비 초과수익 ÷ ADR (찐반등 당일 포함)'),
    ('고점 근접', '52주 고점 근접', '기준일 종가가 52주(252거래일) 최고가에 얼마나 가까운지 — 추세추종 탭과 같은 기준'),
    ('저점 선행', '저점 선행', '조정 구간에서 지수보다 며칠 먼저 바닥을 찍었는지'),
    ('양봉 렐볼', '양봉 렐볼', '최근 5일 안에 거래량 1.5배↑ 양봉이 있으면 만점, 없으면 0'),
    ('장기 추세', '장기 추세 (200일선 이격)', '200일선 위면 만점, 아래라도 이격 0~−15%면 절반, 그보다 멀면 0'),
]


def _score_table(rebound: bool):
    """차기 주도주 배점표 — 하락국면(찐반등 전) / 상승국면(찐반등 이후). 지금 쓰는 쪽을 강조."""
    wc, wr = nl.WEIGHTS['correction'], nl.WEIGHTS['rebound']
    on = 'style="background:rgba(22,163,74,.10);font-weight:700"'

    def cell(w, k, active):
        v = w.get(k)
        return f'<td {on if active else ""}>{v if v else "—"}</td>'

    rows = ''.join(
        f'<tr><td><span class="sl-name">{name}</span><div class="sl-sub" style="margin:0">{desc}</div></td>'
        + cell(wc, k, not rebound) + cell(wr, k, rebound) + '</tr>'
        for k, name, desc in _SCORE_ITEMS)
    total = (f'<tr><td><b>합계</b></td><td {on if not rebound else ""}>{sum(wc.values())}</td>'
             f'<td {on if rebound else ""}>{sum(wr.values())}</td></tr>')
    st.markdown(
        '<div style="margin-top:10px;font-weight:700;font-size:.9rem">점수 배점 (100점)</div>'
        '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>항목</th>'
        f'<th {on if not rebound else ""}>하락국면 (찐반등 전){" · 지금" if not rebound else ""}</th>'
        f'<th {on if rebound else ""}>상승국면 (찐반등 이후){" · 지금" if rebound else ""}</th>'
        f'</tr></thead><tbody>{rows}{total}</tbody></table></div>', unsafe_allow_html=True)
    st.caption('양봉 렐볼을 뺀 항목은 같은 와치리스트 안 상대 순위(1등 만점·꼴찌 0점) × 배점. '
               '종목 점수 70점 이상 = "강한 종목". 섹터 점수 = 종목 점수 중앙값 60% + 강한 종목 비율 40%. '
               '5년 검증에서 역효과였던 거래량 우위·21EMA 위는 넣지 않습니다 (추세추종 탭·순환매와 동일).')


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
    # TradingView 내보내기 섹션을 먼저 비운다 — 아래 어느 조기 return에서도 직전 실행 목록이 남지 않게
    tv_export.register_lead(m.key, [])
    tv_export.register_hot(m.key, [])
    if not _universe(m):
        st.info('와치리스트가 비어 있습니다. 사이드바에서 업로드해 주세요.')
        return
    opts = _asof_options(m)
    if not opts:
        st.warning(f"스냅샷에 {'·'.join(m.bench)} 일봉이 없습니다. 다음 배치 수집 후 표시됩니다.")
        return
    idx_rec = store.load_snapshot('indices').get('data', {}).get(m.phase_index) or {}
    idx_last = (idx_rec.get('dates') or [None])[-1]
    if idx_last and idx_last > opts[0]:
        st.warning(f"⚠️ 종목 데이터가 {opts[0]}까지만 있습니다 ({m.phase_index} 지수는 {idx_last}까지). "
                   "사이드바 '🔄 데이터 재수집'을 누르면 최신 날짜로 계산됩니다.")
    # 기준일 선택 상자는 '지수 현황' 접기 안에 있지만, 그 제목(국면)을 그리려면 기준일이 먼저 필요하다.
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
    # 화면 배치는 국면과 상관없이 고정 — 지수 현황 → 차기 주도섹터 → 차기 주도주 → 🔥 급부상 섹터 → 🔥 급부상 종목 → 자세히 보기.
    # 두 묶음 — 차기 주도(추세추종용) · 급부상(10EMA PP용). 묶음 머리에 사용 방법, 안에 섹터·종목 칸
    tv_export.register_lead(m.key, [])      # 국면 없음·조기 return 때 직전 목록이 남지 않게 먼저 비운다
    if res['phase'] is not None:
        with st.container(border=True):
            st.markdown('#### 🧭 차기 주도 <span style="font-size:.8rem;opacity:.6;font-weight:500">— 추세추종용</span>',
                        unsafe_allow_html=True)
            st.info('**사용 방법**  \n'
                    '**📉 하락국면** (기산점 ~ 찐반등) — 지수가 빠지는 동안 **덜 빠지고 · 52주 고점을 지키고 · 저점을 먼저 찍은** '
                    '섹터·종목을 관심 목록에 둡니다. 이 구간엔 사지 않고 찐반등을 기다립니다.  \n'
                    '**📈 상승국면** (찐반등 이후 ~ 다음 21EMA 이탈) — 관심 목록 중 **찐반등 이후 지수보다 먼저 치고 나가는** '
                    '(상승국면 RS/ADR이 높은) 종목을 추세추종 탭에서 진입 규칙·손절대로 봅니다.', icon='💡')
            with st.expander('**🧭 차기 주도섹터**', expanded=False):
                _lead_sectors(m, res, labels, names)
            with st.expander('**🎯 차기 주도주**', expanded=False):
                _lead_stocks(m, res, labels, names)
    records = cls.load_store(m.key)
    with st.container(border=True):
        st.markdown('#### 🔥 급부상 <span style="font-size:.8rem;opacity:.6;font-weight:500">— 10EMA PP용</span>',
                    unsafe_allow_html=True)
        st.info('**사용 방법**  \n'
                '돈이 몰린 섹터를 알아두고, 그 섹터 종목에서 **6주 안에 10EMA PP 기준봉이나 셋업**이 나오길 기다립니다.',
                icon='💡')
        st.warning('**주의**  \n'
                   '1\\) 급부상 섹터·종목에 없어도 **10EMA 탭에 PP 셋업이 뜰 수 있습니다** — '
                   '🔥는 **우선순위를 높이는 참고**일 뿐 필수 조건이 아닙니다.  \n'
                   '2\\) 급부상 섹터여도 **종목 자체가 조건 밖이면 급부상 종목에 나오지 않습니다** — '
                   '**50·200일선 아래, 편입 후 3 ADR 이상 하락, ADR 6% 미만, 자기 렐볼 없음.**',
                   icon='⚠️')
        with st.expander('**🔥 급부상 섹터**', expanded=False):
            _hot_block(res, labels, names, records)
        with st.expander('**🔥 급부상 종목**', expanded=False):
            _rotation_stocks(m, res, labels, names, records)

    views = ['A/B 판정', '순환매 점수 순위', '섹터 지도', '섹터별 종목', '전체 순위', '분류 수정', '계산 방법']
    with st.expander('자세히 보기 — ' + ' · '.join(views)):
        view = st.segmented_control('보기', views, default=views[0], key=f'{m.key}_detail_view',
                                    label_visibility='collapsed') or views[0]
        main = [s for s in res['sectors'] if s['inMainRanking']]
        if view == 'A/B 판정':
            st.caption('1주·1개월 RS와 10EMA 유지로 주도섹터를 "확정"하는 기준 (A 확인된 주도 / B 차기 후보).')
            _next_sectors(res, labels, names, cls.load_store(m.key))
            _next_stocks(m, res, labels, names)
        elif view == '순환매 점수 순위':
            st.caption('최근 1~2주 순환매 점수로 매긴 섹터 순위 (예전 메인 화면). 갑자기 핫해지는 섹터는 위 🔥 표가 더 빨리 잡습니다.')
            _rotation_sectors(m, res, labels, names, cls.load_store(m.key))
        elif view == '섹터 지도':
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
