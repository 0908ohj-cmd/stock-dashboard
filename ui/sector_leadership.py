"""주도섹터 탭 — 나스닥(210종목 고정 유니버스)·코스피·코스닥 섹터 강도 (마스터 명세 9장).

시세는 배치 스냅샷(data/ohlcv/*.json)만 읽는다. 스냅샷에 없는 종목은 MISSING으로
표시하고 실시간으로 받지 않는다 (명세 3장: 화면에서 일괄 다운로드 금지).
"""
import html
import json
import pathlib
from dataclasses import dataclass

import pandas as pd
import requests
import streamlit as st

from ui import rs_range, tv_export

from data import classification as cls
from data import classification_sync as cls_sync
from data import store
from strategy import next_leaders as nl
from strategy import sector_strength as ss
from strategy import sector_view as sv
from strategy.rs_correction import _index_peak_date, clean_manual_rs_range, resolve_rs_range

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
    rot_stocks = []
    if idx_rec:
        idx_df = store._records_to_df(idx_rec)
        phase = nl.detect_phase(idx_df, asof, peak or None, jjin or None)
        picks = nl.score_stocks(rep.stocks, bars, idx_df, theme_of, main, phase, asof)
        # 섹터 순위는 메인 테마만 ('기타 소비'·'지주·기타' 같은 잡다한 묶음은 섹터로 의미가 없어서). 종목 표에는 전부 나온다.
        in_main = lambda pool: [p for p in pool if p['primaryTheme'] in main]
        ranked, thin = nl.rank_sectors(in_main(picks))
        # 급부상 종목 후보(hot_stock_pool)의 재료 — 순환매 종목 점수·PP 가능성 점수·관문 값.
        # 2주 전 순환매 재계산·순환매 섹터 순위(rotation_sectors)는 쓰는 화면이 없어져 뺐다(#46)
        rot_stocks = nl.rotation_scores(rep.stocks, bars, idx_df, theme_of, main, asof)
    # 비메인 테마('지주·기타' 등)도 🔥 판정에 넣는다 — 2026-11-18 사용자 결정으로 #41 L11(제외)을 되돌림.
    # 화면에서는 테마 토글(기본 꺼짐)로 숨겨 두고, 켜면 보인다. 차기 주도섹터 순위는 그대로 메인만.
    hot = nl.hot_sectors(bars, theme_of, asof)
    bull = nl.bull_since({t: bars[t] for t in theme_of if t in bars}, asof)
    # 단독 렐볼 판정의 섹터 크기 = 🔥 판정과 같은 그룹(hot_groups) — 두 판정이 다른 크기를 써서 어느 쪽에도
    # 안 잡히는 종목이 생기지 않게(#41 M7)
    sizes = {th: len(ts) for th, ts in nl.hot_groups(bars, theme_of, asof).items()}
    return {
        'asof': rep.asof,
        'bench': {'names': list(rep.bench.names), 'ret5': rep.bench.ret5, 'ret21': rep.bench.ret21,
                  'blend5': rep.bench.blend5, 'blend21': rep.bench.blend21},
        'sectors': rep.sectors, 'coverage': rep.coverage,
        'phase': phase, 'picks': picks, 'nextSectors': ranked, 'thinSectors': thin,
        'lowAdr': sorted(low_adr), 'adrMin': adr_min,
        'hot': hot, 'themeOf': dict(theme_of), 'bullSince': bull,
        # 급부상 종목 후보·관문 — 규칙은 next_leaders.hot_stock_pool 한 곳 (#41 M9)
        'hotPool': nl.hot_stock_pool(rot_stocks, hot, sizes, bull, bars, rep.asof),
    }


SCORE_VERSION = 2026_11_18_2 # next_leaders 점수·필드가 바뀌면 올릴 것 (캐시 무효화)


def hot_history(market_key: str) -> tuple[dict, dict]:
    """(🔥 섹터 {테마: 정보}, {테마: 라벨}) — 최신 기준일. 10EMA 탭 배지용 🔥 판정 단일 출처.
    종목→테마는 넘기지 않는다 — _compute의 지도는 이 탭 유니버스(US 210종목)만 담아, 10EMA 종목의
    62%(유니버스 밖)에 배지가 안 붙었다. 받는 쪽이 cls.primary_map으로 직접 찾는다(#42 M12)."""
    m = MARKETS[market_key]
    opts = _asof_options(m)
    if not opts:
        return {}, {}
    try:
        res = _compute(m.key, opts[0], _data_key(m))
    except ValueError:
        return {}, {}
    return res.get('hot') or {}, _labels(m)


def hot_badge(info: dict, label: str) -> str:
    """'🔥 광통신 (지금)' / '🔥 광통신 10/20'."""
    when = ('지금' if info['now'] else '6주 전부터' if info['last'] is None else f"{info['last']:%m/%d}")
    return f"🔥 {label} ({when})"


def lead_scores(market_key: str, peak: str = '', jjin: str = '', asof: str = '') -> dict:
    """차기 주도주 점수를 다른 탭(추세추종)에서 그대로 쓰기 위한 단일 출처.
    {ticker: {'score', 'rank', 'parts', 'asof', 'phaseLabel'}} — 순위는 메인 밖 테마 포함 전체 기준.
    계산은 주도섹터 탭과 같은 _compute(같은 캐시)를 쓰므로 두 탭 점수가 어긋날 수 없다.
    asof를 비우면 최신일 — 주도섹터 탭 '자세히 보기'는 그 탭이 고른 기준일을 넘긴다(#42 M11)."""
    m = MARKETS[market_key]
    opts = _asof_options(m)
    if not opts:
        return {}
    asof = asof or opts[0]
    # 주도섹터 탭과 같은 정리 — 계산 기준일보다 뒤인 수동 날짜는 뺀다(#42 L8). 같은 문자열 → 같은 캐시
    p_, j_, _ = clean_manual_rs_range(peak or None, jjin or None, asof)
    peak, jjin = rs_range.date_str(p_), rs_range.date_str(j_)
    try:
        res = _compute(m.key, asof, _data_key(m), peak, jjin)
    except ValueError:
        return {}
    picks = sorted(res['picks'], key=lambda p: -p['nextScore'])
    ph = res.get('phase') or {}
    label = ph.get('label', '')
    has_jjin = ph.get('jjinDate') is not None     # 찐반등 전엔 상승국면 값 없음 — 주도섹터 탭과 같게
    return {p['ticker']: {'score': p['nextScore'], 'rank': i, 'parts': p.get('nextParts', {}),
                          'asof': res['asof'], 'phaseLabel': label,
                          'downAdr': p.get('sincePeakExcessAdr'),
                          'upAdr': p.get('sinceJjinExcessAdr') if has_jjin else None,
                          'sma200': sma200_text(p.get('sma200Rising'), p.get('gap200'))}
            for i, p in enumerate(picks, 1)}


def sma200_text(up, gap) -> str | None:
    """표(AgGrid·dataframe)용 '200일선' 칸 — '↗ 상승 (+12.3%)' / '↘ 하락 (−5.0%)'. 방향이 장기 추세 점수, 괄호는 이격."""
    if up is None:
        return None
    return ('↗ 상승' if up else '↘ 하락') + ('' if gap is None else f' ({gap:+.1f}%)')


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


def _num(v, digits=2, signed=True):
    if v is None:
        return '—'
    return f'{v:+.{digits}f}' if signed else f'{v:.{digits}f}'


# ── 핵심 화면: 차기 주도섹터 · 차기 주도주 ─────────────────
# 판정 기준은 뉴스나 하루 상승폭이 아니라 1주·1개월 상대강도와 10EMA 위 유지 비율이다.

_CSS = """<style>
.sl-wrap{overflow-x:auto;margin:4px 0 18px}
/* 표 바로 아래 붙는 설명 — 같은 st.markdown 안에 넣어 표와 붙이고, ↳로 '위 표 설명'임을 보인다 */
.sl-wrap.has-note{margin-bottom:4px}
.sl-note{font-size:.82rem;line-height:1.7;margin:0 0 18px 2px}
.sl-note div{padding-left:1.5em;text-indent:-1.5em;opacity:.7}
.sl-note .arr{display:inline-block;width:1.5em;text-indent:0;font-size:1.05rem;font-weight:700;opacity:1}
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


def _esc(v) -> str:
    return html.escape(str(v))


def _signed(v, unit='%p', digits=1) -> str:
    if v is None:
        return '<span class="sl-sub">—</span>'
    cls_ = 'sl-pos' if v > 0 else 'sl-neg' if v < 0 else ''
    return f'<span class="{cls_}">{v:+.{digits}f}{unit}</span>'


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


def _bar_cell(v: float, scale: float = 100, color: str = '#16a34a', text: str | None = None, sub: str = '') -> str:
    """숫자 + 가는 막대 — 메인 네 표의 점수·비율 칸 공통 모양. v는 0~scale."""
    w = 0 if v is None else max(0.0, min(1.0, v / scale)) * 100
    return (f'<div class="hb-num"><b>{text if text is not None else f"{v:.0f}"}</b>{sub}</div>'
            f'<div class="hb-bar"><i style="width:{w:.0f}%;background:{color}"></i></div>')


def _colgroup(widths: list) -> str:
    return '<colgroup>' + ''.join(f'<col style="width:{w}%">' for w in widths) + '</colgroup>'


_NEW_TAG = ' <span class="sl-badge sl-A" style="font-size:.66rem;padding:1px 6px;vertical-align:1px">NEW</span>'


def _stock_title(t: str, nm: str, new: bool = False) -> str:
    """종목 칸 — 이름 굵게(+ 바로 옆 NEW), 아래 줄에 티커."""
    tag = _NEW_TAG if new else ''
    return (f'<div class="sl-name">{_esc(nm)}{tag}</div><div class="two">{_esc(t)}</div>' if nm != t
            else f'<div class="sl-name">{_esc(t)}{tag}</div>')


def _note(*lines: str) -> str:
    """표 바로 아래 설명(HTML) — 표와 같은 st.markdown에 이어 붙여 간격 없이 붙인다. 줄마다 ↳.
    표 쪽 <div class="sl-wrap">는 'sl-wrap has-note'로 써야 아래 여백이 줄어 붙어 보인다."""
    body = ''.join(f'<div><span class="arr">↳</span>{ln}</div>' for ln in lines if ln)
    return f'<div class="sl-note">{body}</div>' if body else ''


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
        if h.get('latePass'):
            state += f'<div class="two">늦은 통과 ({h["alignedPassDate"]:%m/%d})</div>'
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
                '<th>정배열 종목 비율 (오늘)</th><th>현재 렐볼 양봉 비율</th><th>최근 5일 렐볼 양봉 종목</th></tr></thead><tbody>'
                + ''.join(rows) + '</tbody></table></div>', unsafe_allow_html=True)
    _hot_legend()


def _hot_legend():
    """🔥 급부상 섹터 표 아래 '🔥 기준과 표시 설명' — 🔥 조건과 상태·늦은 통과의 뜻을 표 하나로."""
    cond = (f'두 조건을 <b>모두</b> 만족한 날부터 🔥입니다.<br>'
            f'① 섹터 종목({nl.MIN_SECTOR_N}개 이상)의 <b>{100 * nl.HOT_SHARE:.0f}% 이상</b>이 최근 5일 안에 '
            f'거래량 {nl.HOT_RVOL:g}배↑ 양봉<br>'
            f'② 섹터 종목의 <b>{100 * nl.HOT_MIN_ALIGNED:.0f}% 이상</b>이 정배열(10EMA &gt; 21EMA &gt; 50일선)')
    rows = [
        ('<b>🔥 급부상 섹터</b>', cond),
        ('<span class="sl-badge sl-A">지금 🔥</span>', '오늘도 ①을 만족 — 돈이 몰리는 중'),
        ('<span class="sl-badge sl-C">꺼짐 · N거래일 전 시작</span>',
         f'🔥였다가 지금은 쉬는 중 — PP 기준봉을 기다릴 구간. 눌림으로 정배열이 풀려도 약 6주({nl.HOT_LOOKBACK}거래일) 표시'),
        ('<span class="sl-badge sl-C">꺼짐 · 6주보다 전에 시작</span>', '6주보다 오래전에 시작된 🔥 — 시작일을 알 수 없고 지금은 꺼짐'),
        ('<span class="two" style="display:inline">늦은 통과 (MM/DD)</span>',
         '① 거래량이 먼저 터지고, ② 정배열 20%는 MM/DD에 뒤늦게 충족된 섹터 — 바닥에서 올라온 경우'),
    ]
    body = ''.join(f'<tr><td style="white-space:nowrap">{k}</td><td>{v}</td></tr>' for k, v in rows)
    st.markdown('<div style="margin-top:10px;font-weight:700;font-size:.9rem">🔥 기준과 표시 설명</div>'
                '<div class="sl-wrap"><table class="sl-t"><thead><tr><th>표시</th><th>뜻</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>', unsafe_allow_html=True)


def _rotation_stocks(m: Market, res: dict, labels: dict, names: dict, records: dict):
    # 🔥 급부상 섹터(지금 또는 최근 6주) 소속 종목만 — 정렬은 PP 가능성 점수
    tv_export.register_hot(m.key, [])       # 조기 return 때 직전 목록이 남지 않게 먼저 비운다
    hot = res.get('hot') or {}
    # 후보(🔥 섹터 종목 + 단독 렐볼)와 탈락·관문 규칙은 next_leaders.hot_stock_pool — 여기선 토글 필터·표시만 (#41 M9)
    hp = res.get('hotPool') or {}
    pool = hp.get('rows') or []
    if not pool:        # 후보가 없는지, 탈락 규칙으로 다 빠졌는지 구분해 안내 (#42 L9)
        st.caption(nl.hot_pool_message(hp))
        return
    in_trend = _trend_tickers(m)
    bar = st.container(horizontal=True, gap='medium')      # 차기 주도주 토글과 같은 배치
    only_trend = bar.toggle('추세추종 탭 종목만', key=f'{m.key}_rot_only_trend', width='content')
    show_weak = bar.toggle(f'{nl.PP_MIN_SCORE}점 미만도 보기', key=f'{m.key}_rot_show_weak', width='content',
                           help=f'PP 가능성 점수 {nl.PP_MIN_SCORE}점 미만 종목도 표에 넣습니다 (기본은 숨김)')
    if not show_weak:
        pool = [r for r in pool if not r['weak']]
    # 차기 주도주와 같은 토글 목록(비메인 포함, 기본 꺼짐) — 2026-11-18 사용자 결정으로 #41 L11 되돌림
    hidden = _theme_toggles(m, bar, labels, {r['primaryTheme'] for r in pool}, key_prefix=f'{m.key}_rot_show')
    rows = sorted((r for r in pool
                   if (not only_trend or r['ticker'] in in_trend)
                   and r['primaryTheme'] not in hidden),
                  key=lambda r: -r['ppScore'])[:30]
    tv_export.register_hot(m.key, [r['ticker'] for r in rows], res['asof'])   # 화면 표 그대로, 제목에 기준일
    if not rows:        # 50점 미만만 남았으면 그 안내, 아니면 토글로 숨긴 것
        st.caption(nl.hot_pool_message(hp, show_weak) or '조건에 맞는 종목이 없습니다 — 위 토글로 숨긴 종목만 남았습니다.')

    body = []
    for rank, r in enumerate(rows, 1):
        t = r['ticker']
        nm = _stock_name(t, names, records)
        title = _stock_title(t, nm, r['isNew'])     # NEW = 편입일(지금 이어지는 🔥 구간 시작·단독 렐볼 첫 렐볼 양봉)이 오늘
        if t not in in_trend:
            title += '<div class="sl-off">추세추종 탭에 없음</div>'
        if r['solo']:
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
            '<div class="sl-wrap has-note"><table class="sl-t fx" style="min-width:900px">' + _colgroup([5, 17, 21, 9, 10, 7, 9, 9, 13])
            + '<thead><tr><th>순위</th><th>종목</th><th>섹터 · 테마</th>'
            '<th>52주 고점 대비</th><th>10EMA 최근 5일</th><th>정배열</th><th>1주 RS/ADR</th>'
            '<th>양봉 렐볼 (최근 5일)</th><th>PP 가능성 점수</th></tr></thead><tbody>' + ''.join(body)
            + '</tbody></table></div>'
            + _note('🔥 지금은 오늘도 급부상 중인 종목이며, 해당 순위는 매수 신호가 아닙니다. '
                    '— 10EMA PP 기준봉이나 셋업이 나올 때까지 관찰하세요.',
                    f'섹터 종목 부족 · 단독 렐볼은 같은 섹터 종목이 {nl.MIN_SECTOR_N}개 미만이라 섹터 🔥 판정에서는 빠지지만, '
                    '최근 5일 안에 종목 자체에 렐볼 양봉이 나와 함께 보여주는 종목입니다.'),
            unsafe_allow_html=True)
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


def _md(d) -> str:
    """배너 날짜 — 없으면 '—'. None을 그대로 포맷하면 TypeError로 이후 탭이 모두 멈춘다(#29)."""
    return f'{d:%m/%d}' if d is not None else '—'


def _pct1(v) -> str:
    return f'{v:+.1f}%' if v is not None else '—'


def _phase_banner(m: Market, res: dict):
    ph = res['phase']
    idx = m.phase_index
    manual = ' · 수동 설정' if ph.get('manual') else ''
    if ph['mode'] == 'correction':
        sp = ph['indexSincePeak']
        body = (f"<b>🔴 조정 중 ({ph['label']}{manual})</b> — 기산점 {_md(ph['peakDate'])} 이후 {idx} "
                f"<b>{_pct1(sp)}</b>.<br>지금은 주도섹터가 '확정'될 수 없는 구간입니다. 대신 <b>지수가 빠지는 동안 "
                "덜 빠지고, 고점을 지키고, 먼저 저점을 찍은</b> 섹터·종목을 찾습니다 — 찐반등이 나오면 이들이 먼저 튑니다.")
    elif ph['mode'] == 'rebound':
        sj = ph['indexSinceJjin']
        body = (f"<b>{_rebound_ball(ph['label'])} 찐반등 {ph['label']}{manual}</b> — 기산점 {_md(ph['peakDate'])} · 찐반등일 {_md(ph['jjinDate'])} 이후 {idx} <b>{_pct1(sj)}</b>.<br>"
                "조정 중 버틴 종목 가운데 <b>찐반등 이후 지수보다 먼저 치고 나가는</b> 종목이 진짜 주도주입니다. "
                "회복 속도에 가장 큰 비중을 둡니다.")
    else:
        last = (f" — 마지막 찐반등 {ph['jjinDate']:%m/%d} 이후 {idx} <b>{ph['indexSinceJjin']:+.1f}%</b>"
                if ph.get('jjinDate') is not None and ph.get('indexSinceJjin') is not None else '')
        body = (f"<b>🟢 상승 추세</b>{last}.<br>차기 주도섹터·주도주는 다음 조정(21EMA 이탈) 전까지 마지막 기산점·찐반등 기준을 "
                "그대로 이어 갑니다. 갑자기 돈이 몰리는 섹터는 아래 <b>'🔥 급부상 섹터'</b>에서 봅니다.")
    late = ' late' if ph['mode'] == 'rebound' and _rebound_ball(ph['label']) == '🔵' else ''
    st.markdown(_CARD_CSS + f'<div class="nl-phase {ph["mode"]}{late}">{body}</div>', unsafe_allow_html=True)
    if ph.get('warning'):       # 수동 기산점·찐반등일을 무시·조정한 경우 (detect_phase가 이유를 넘긴다)
        st.warning(f"**RS 구간 설정** — {ph['warning']}", icon='⚠️')


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
        thin_note = (_note('표본 부족(3종목 미만)으로 순위 제외: '
                           + ', '.join(f"{_esc(labels[s['theme']])}({s['n']})" for s in thin)) if thin else '')
        st.markdown(
            f'<div class="sl-wrap{" has-note" if thin_note else ""}"><table class="sl-t fx" style="min-width:980px">'
            + _colgroup([5, 13, 10] + [9] * len(cols) + [9, 13, 13, 19])
            + '<thead><tr><th>순위</th><th>섹터</th><th>점수</th>'
            + ''.join(f'<th>{_th_two(c[0])}</th>' for c in cols) + '<th>' + _th_two('52주 고점 대비 (중앙값)') + '</th><th>' + _th_two('10EMA 위 종목 비율 (2주전→1주전→기준일)') + '</th><th>' + _th_two('21EMA 위 종목 비율 (2주전→1주전→기준일)') + '</th><th>대표주</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>' + thin_note, unsafe_allow_html=True)


_THEME_TOGGLES = {'KR_KOSPI': ('BIO_PHARMA', 'FINANCE'), 'KR_KOSDAQ': ('BIO_PHARMA', 'FINANCE')}


def _theme_toggles(m: Market, bar, labels: dict, present: set, key_prefix: str, non_main: bool = True) -> set:
    """테마별 켜고 끄는 토글 — 차기 주도주·순환매 종목 표 공통. 끈(숨길) 테마 코드 집합을 돌려준다.
    국장 바이오·제약·금융 + 메인 밖 테마(KR 지주·기타, US 바이오·헬스케어·금속·소재 등), 모두 기본 꺼짐.
    두 표가 같은 토글 목록을 보이도록 표에 그 테마 종목이 없어도 항상 보여준다 (present는 쓰지 않음).
    non_main=False: 메인 밖 테마 토글은 뺀다 — 급부상 종목 표는 비메인 테마 종목이 아예 없다(#41 L11)."""
    side = sv.theme_toggle_codes(_THEME_TOGGLES.get(m.key, ()), cls.load_taxonomy(m.key)['themes'],   # 중복 제거(#42 L10)
                                 non_main=non_main)
    return {code for code in side
            if not bar.toggle(labels.get(code, code), value=False, key=f'{key_prefix}_{code}', width='content',
                              help='켜면 이 테마 종목도 표에 넣습니다 (기본은 빠져 있음)')}


def _prev_ranks(m: Market, res: dict, peak: str, jjin: str, keep) -> tuple[dict | None, bool]:
    """(직전 거래일 같은 조건(토글 포함)의 차기 주도주 순위 {티커: 순위}, 찐반등 기간인가).
    순위 등락은 늘, NEW(어제 상위 20 밖 → 오늘 진입)는 찐반등 기간에만 쓴다.
    찐반등 당일도 비교한다(전날은 조정 공식 순위) — 찐반등과 함께 새로 들어온 종목이 NEW로 보인다."""
    ph = res['phase']
    opts = _asof_options(m)
    if res['asof'] not in opts:
        return None, False
    i = opts.index(res['asof'])
    if i + 1 >= len(opts):
        return None, False
    prev_asof = opts[i + 1]
    rebound = (ph.get('jjinDate') is not None
               and pd.Timestamp(prev_asof) >= pd.Timestamp(ph.get('jjinBase') or ph['jjinDate']))
    # 전일 화면과 같은 조건 — 수동 날짜도 전일 기준일로 다시 정리한다(#42 L8). 안 하면 오늘 찐반등일이
    # 전일로 당겨져(detect_phase._snap) '어제가 찐반등'인 순위나, 기산점 = 전일인 빈 구간과 비교하게 된다
    p_, j_, _ = clean_manual_rs_range(peak or None, jjin or None, prev_asof)
    try:
        prev = _compute(m.key, prev_asof, _data_key(m), rs_range.date_str(p_), rs_range.date_str(j_))
    except ValueError:
        return None, rebound
    prev_top3 = {s['theme'] for s in prev['nextSectors'][:3]}
    rows = sorted((r for r in prev['picks'] if keep(r, prev_top3)), key=lambda r: -r['nextScore'])
    return {r['ticker']: k for k, r in enumerate(rows, 1)}, rebound


def _rank_cell(rank: int, prev: int | None, known: bool) -> str:
    """순위 + 전일 대비 등락 (▲ 올라옴 초록, ▼ 내려감 빨강, – 같음, 전일 순위 없음은 표시 안 함)."""
    num = f'<span style="opacity:.55">{rank}</span>'
    if not known or prev is None:
        return num
    d = prev - rank
    mark = (f'<span class="sl-pos">▲{d}</span>' if d > 0 else f'<span class="sl-neg">▼{-d}</span>' if d < 0
            else '<span style="opacity:.6">–</span>')
    return f'{num}<div class="two" style="opacity:1;white-space:nowrap">{mark}</div>'


def _lead_stocks(m: Market, res: dict, labels: dict, names: dict, peak: str = '', jjin: str = ''):
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
    def keep(r, top3):
        return ((not only_top or r['primaryTheme'] in top3)
                and (not only_trend or r['ticker'] in in_trend)
                and r['primaryTheme'] not in hidden)

    rows = sorted((r for r in picks if keep(r, top_sectors)), key=lambda r: -r['nextScore'])[:20]
    tv_export.register_lead(m.key, [r['ticker'] for r in rows], res['asof'])  # 화면 표 그대로(토글 반영, 상위 20), 제목에 기준일
    prev_rank, rebound_now = _prev_ranks(m, res, peak, jjin, keep)
    # NEW — 찐반등 기간에만: 어제 상위 20에 없던 종목
    prev_top = ({t for t, k in prev_rank.items() if k <= 20} if prev_rank is not None and rebound_now else None)
    if not rows:
        st.caption('조건에 맞는 종목이 없습니다.')
        _score_table(rebound)
        return
    body = []
    for rank, r in enumerate(rows, 1):
        t = r['ticker']
        nm = _stock_name(t, names, records)
        title = _stock_title(t, nm, prev_top is not None and t not in prev_top)
        if t not in in_trend:
            title += '<div class="sl-off">추세추종 탭에 없음</div>'
        star = f' <span style="color:#16a34a">{_STAR}</span>' if r['primaryTheme'] in top_sectors else ''
        detail = (records.get(t) or {}).get('detail', '')
        lead = r.get('leadDays')
        lead_txt = '—' if lead is None else (f'{lead}일 먼저' if lead > 0 else '같이' if lead == 0 else f'{-lead}일 늦게')
        body.append(
            f'<tr><td class="rk" style="opacity:1">'
            f'{_rank_cell(rank, (prev_rank or {}).get(t), prev_rank is not None)}</td><td>{title}</td>'
            f'<td>{_esc(labels.get(r["primaryTheme"], ""))}{star}<div class="two">{_esc(detail)}</div></td>'
            + ''.join(f'<td class="hb-num">{_rs_val(c, r, False)}</td>' for c in cols)
            + f'<td class="hb-num">{_signed(r.get("offHigh"), "%")}</td><td class="hb-num">{_sma200_cell(r)}</td>'
            f'<td>{lead_txt}</td>'
            + f'<td>{"✓" if r.get("hasBullishRvol15InLast5") else ""}</td>'
            f'<td>{_bar_cell(r["nextScore"])}</td></tr>')
    st.markdown(
        '<div class="sl-wrap has-note"><table class="sl-t fx" style="min-width:980px">'
        + _colgroup([5, 15, 18] + [9] * len(cols) + [8, 8, 8, 8, 12])
        + '<thead><tr><th>순위</th><th>종목</th><th>섹터 · 테마</th>'
        + ''.join(f'<th>{_th_two(c[0])}</th>' for c in cols) + '<th>52주 고점 대비</th><th>200일선</th><th>저점</th>'
        '<th>양봉 렐볼 (최근 5일)</th><th>점수</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
        + _note(f'<span style="color:#16a34a">{_STAR}</span>은 상위 3개 섹터 소속이며, 해당 순위는 매수 신호가 아닙니다. '
                '— 찐반등 확인 후 5분봉 렐볼 기준봉에서 진입하세요.',
                'NEW = 찐반등 이후, 직전 거래일 상위 20에 없다가 오늘 새로 들어온 종목.' if prev_top is not None else ''),
        unsafe_allow_html=True)
    _score_table(rebound)


_SCORE_ITEMS = [
    # (배점 키, 항목, 설명)
    ('덜 빠짐', '하락국면 RS/ADR', '기산점 ~ 찐반등(찐반등 전엔 ~ 기준일) 지수 대비 초과수익 ÷ ADR'),
    ('찐반등 후 회복', '상승국면 RS/ADR', '찐반등 전날 종가 ~ 기준일 지수 대비 초과수익 ÷ ADR (찐반등 당일 포함)'),
    ('고점 근접', '52주 고점 근접', '기준일 종가가 52주(252거래일) 최고가에 얼마나 가까운지 — 추세추종 탭과 같은 기준'),
    ('저점 선행', '저점 선행', '조정 구간에서 지수보다 며칠 먼저 바닥을 찍었는지'),
    ('양봉 렐볼', '양봉 렐볼', '최근 5일 안에 거래량 1.5배↑ 양봉이 있으면 만점, 없으면 0'),
    ('장기 추세', '장기 추세 (200일선 방향)',
     '오늘 200일선이 20거래일 전 200일선보다 높으면(상승 중) 만점, 아니면 0. 주가가 200일선 아래여도 200일선이 오르는 중이면 만점'),
]


def _sma200_cell(r: dict) -> str:
    """차기 주도주 표 '200일선' 칸 — 첫 줄 방향(장기 추세 점수), 둘째 줄 (이격)."""
    up, gap = r.get('sma200Rising'), r.get('gap200')
    if up is None:
        return '—'
    mark = '<span class="sl-pos">↗ 상승</span>' if up else '<span class="sl-neg">↘ 하락</span>'
    sub = '' if gap is None else f'<div class="two">(이격 {gap:+.1f}%)</div>'   # 색 없이 — 색은 방향(점수)만
    return f'<span style="white-space:nowrap">{mark}</span>{sub}'


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


# ── 상세 보기 ─────────────────────────────────────────────

def _drilldown(m: Market, res: dict, labels: dict, names: dict, peak: str = '', jjin: str = ''):
    """섹터별 종목 — 추세추종 탭과 같은 열(같은 행 빌더 _build_rows + 같은 주도주 점수 lead_scores)."""
    from ui import watchlist as wl      # watchlist가 이 모듈을 import하므로 함수 안에서 (순환 import 방지)
    sectors = sorted(res['sectors'], key=lambda s: labels.get(s['theme'], s['theme']))
    if not sectors:
        st.caption('분류된 섹터가 없습니다.')
        return
    opts = {f"{labels[s['theme']]} ({s['memberCount']}종목)": s for s in sectors}
    pick = st.selectbox('섹터 선택', list(opts), key=f'{m.key}_drill')
    s = opts[pick]
    idx_ts = wl._snapshot_ts('indices')
    status = wl._get_market_status_cached(m.key, idx_ts)
    cs, jd = status['correction_start'], status['jjin_date']
    # 저점선행·양봉비 구간은 추세추종 탭과 같은 규칙(resolve_rs_range) — 수동 값을 그대로 넘기면 수동 기산점이
    # 자동 찐반등일과 같거나 뒤일 때 빈 구간이 됐다(#40 M5)
    idx = wl._fetch_index_cached(m.phase_index, idx_ts)
    auto_peak = _index_peak_date(idx, cs) if cs is not None and not idx.empty else None
    custom_peak, rs_jjin, _ = (resolve_rs_range(idx.index, peak or None, jjin or None, auto_peak, jd)
                               if not idx.empty else (None, jd, None))
    # 종가·등락·고점대비·이평선위치와 주도주 점수는 위쪽 표와 같은 기준일(res['asof'])로 — 최신일로 두면
    # 과거 기준일을 골랐을 때 같은 종목에 두 값이 보였다(#42 M11)
    rows = wl._build_rows(tuple(sorted(s['members'])), m.key,
                          str(cs.date()) if cs else None, rs_range.date_str(rs_jjin) or None,
                          rs_range.date_str(custom_peak) or None, res['asof'], None,
                          wl._snapshot_ts(m.key), idx_ts)
    if not rows:
        st.caption('표시할 종목이 없습니다 (데이터 부족 또는 ADR 필터).')
        return
    lead = lead_scores(m.key, peak, jjin, res['asof'])
    for r in rows:
        x = lead.get(r['Ticker']) or {}
        r['점수'] = None if x.get('score') is None else round(x['score'], 1)
        r['하락RS'] = None if x.get('downAdr') is None else round(x['downAdr'], 2)
        r['상승RS'] = None if x.get('upAdr') is None else round(x['upAdr'], 2)
        r['200일선'] = x.get('sma200')
    rows.sort(key=lambda r: (r['점수'] is None, -(r['점수'] or 0)))
    for i, r in enumerate([r for r in rows if r['점수'] is not None], 1):
        r['순위'] = i
    df = pd.DataFrame([{
        '티커 | 종목명': f"{r['Ticker']} | {r['종목명']}",
        '주도주 점수': r['점수'], '주도주 순위': r.get('순위'),
        '섹터': r['섹터'], '테마': r['테마'], '종가': r['Close'], '등락%': r['등락%'],
        '하락국면 RS/ADR': r['하락RS'], '상승국면 RS/ADR': r['상승RS'], '200일선': r['200일선'],
        '이평선위치': r['이평선위치'], '저점선행(일)': r['저점선행'], '양봉비%': r['양봉비%'], '고점대비%': r['고점대비%'],
    } for r in rows])
    st.caption(f"{labels[s['theme']]} {len(rows)}종목 — 추세추종 탭과 같은 열·같은 주도주 점수 (순위는 이 섹터 안). "
               '구성은 분류 기록(주력 테마) 기준이고, ADR 필터(국장 2%·미장 4%) 미만은 빠집니다.')
    money = '₩%d' if m.key.startswith('KR') else '$%.2f'
    st.dataframe(df, hide_index=True, width='stretch',
                 column_config={'종가': st.column_config.NumberColumn(format=money)})


def _github_token() -> str:
    try:
        return st.secrets.get('GITHUB_TOKEN', '')
    except Exception:
        return ''


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
        # 고를 값이 없으면 빈 선택 — 미분류로 채우면 이유만 적고 저장해도 미분류 확정이 생긴다(#37)
        cur = cls.review_default(rec)
        primary = st.selectbox('주력 테마', codes,
                               index=codes.index(cur) if cur in codes else None,
                               placeholder='주력 테마를 고르세요', format_func=lambda c: labels[c])
        secondary = st.multiselect('보조 태그', [c for c in codes if c != 'UNCLASSIFIED'],
                                   default=[c for c in rec.get('secondaryThemes', []) if c in codes],
                                   format_func=lambda c: labels[c])
        reason = st.text_input('수정 이유 (필수)')
        if st.form_submit_button('확정 저장 — 이후 자동 분류가 덮어쓰지 않음'):
            if primary is None:
                st.error('주력 테마를 골라 주세요. 근거가 부족하면 미분류를 직접 고르면 됩니다.')
            elif not reason.strip():
                st.error('수정 이유를 적어 주세요.')
            else:
                def apply(recs):
                    cls.set_override(m.key, t, primary, secondary, reason, recs)

                local = dict(records)
                apply(local)
                # 원격 내용 위에 이 종목만 바꿔 커밋한다 — 로컬 파일 전체를 올리면 그 사이 origin에
                # 들어온 다른 확정·신규 분류가 덮인다(#33). 성공하면 로컬도 올린 내용으로 맞춘다
                res = cls_sync.commit_store_change(m.key, apply, token=_github_token(), repo=_GITHUB_REPO,
                                                   message=f'data: {t} 테마 수동 확정', http=requests)
                cls.save_store(m.key, res.records if res.ok else local)
                # 분류를 쓰는 캐시만 비운다 — st.cache_data.clear()는 앱 전체·전 사용자 캐시(10EMA 3탭 수 분 스캔)를
                # 날렸다(#42 M13). _compute(이 탭·lead_scores·hot_history)는 분류 mtime이 캐시 키(_data_key)에 있어
                # 저절로 다시 계산되고, 키에 분류가 없는 추세추종 행(섹터·테마 열)만 비운다. 10EMA 스캔은 두므로
                # 그 섹터·테마 열은 TTL(1시간)·다음 배치에 맞춰진다 (🔥 배지는 primary_map이라 바로 반영)
                from ui import watchlist as wl      # watchlist가 이 모듈을 import하므로 함수 안에서 (순환 import 방지)
                wl._build_rows.clear()
                (st.success if res.ok else st.warning)(f'저장했습니다 — {res.message}')


@st.cache_data(ttl=1800, show_spinner=False)
def _auto_phase(index_name: str, asof: str, idx_key: int) -> dict | None:
    rec = store.load_snapshot('indices').get('data', {}).get(index_name)
    if not rec:
        return None
    return nl.detect_phase(store._records_to_df(rec), asof)


def _rs_range(m: Market, asof: str) -> tuple[str, str]:
    """추세추종 탭과 같은 '⚙️ RS 구간 설정' — 같은 공유 키(ui.rs_range)라 한쪽에서 지정하면 두 탭이 같은 값으로
    계산한다(#40 M5). 반환: (기산점, 찐반등일) 문자열, 자동이면 ''."""
    auto = _auto_phase(m.phase_index, asof, _mtime(store._snapshot_path('indices'))) or {}
    # 고를 수 있는 마지막 날 = 최신일(추세추종 탭과 같은 지수의 마지막 날)로 고정 — 기준일로 두면 과거 기준일을
    # 고를 때 Streamlit이 범위 밖 값을 None으로 지워 설정이 사라졌다(#42 L8). 기준일보다 뒤인 날짜는
    # clean_manual_rs_range가 이 기준일 계산에서만 빼고 알린다.
    idx_dates = (store.load_snapshot('indices').get('data', {}).get(m.phase_index) or {}).get('dates') or [asof]
    latest = pd.Timestamp(max(idx_dates[-1], asof)).date()
    raw_peak, raw_jjin = rs_range.rs_range_inputs(
        m.key, 'sector', auto.get('autoPeakDate'), auto.get('autoJjinDate'),
        max_date=latest, note='기준일이 바뀌어도 설정은 유지됩니다.')
    peak, jjin, warns = clean_manual_rs_range(raw_peak, raw_jjin, asof)
    rs_range.show_warnings(warns)
    return rs_range.date_str(peak), rs_range.date_str(jjin)


def _pin_key(market_key: str) -> str:
    return f'{market_key}_asof_pin'


def _pin_asof(market_key: str, latest: str):
    """기준일 상자를 사용자가 바꿨을 때만 불린다(on_change) — 고른 날을 기억하고, 최신일을 고르면 '최신 따라가기'."""
    st.session_state[_pin_key(market_key)] = sv.pin_asof(st.session_state[f'{market_key}_asof'], latest)


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
    # 사용자가 상자에서 직접 고른 과거 날짜(on_change로 기억)만 유지하고, 나머지는 매 실행 최신일을 쓴다 —
    # 세션에 남은 값을 그대로 쓰면 새 배치가 들어와도 이 탭과 TV 내보내기만 전날 기준에 남는다(#42 M10).
    key_asof = f'{m.key}_asof'
    asof = sv.resolve_asof(opts, st.session_state.get(_pin_key(m.key)))
    st.session_state[key_asof] = asof
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
                       on_change=_pin_asof, args=(m.key, opts[0]),
                       help='과거 날짜를 고르면 그날 종가까지만 써서 다시 계산합니다 (최신일을 고르면 새 배치를 따라갑니다)')
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
                _lead_stocks(m, res, labels, names, peak, jjin)
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

    # 2026-11-11 정리 — A/B 판정·순환매 점수 순위·섹터 지도·전체 순위·계산 방법은 화면에서 뺐다
    # (차기 주도·급부상으로 대체되거나 겹침). 그 렌더 함수와 그것만 쓰던 _compute 필드는 #46에서 지웠다.
    views = ['섹터별 종목', '분류 수정']
    with st.expander('자세히 보기 — ' + ' · '.join(views)):
        view = st.segmented_control('보기', views, default=views[0], key=f'{m.key}_detail_view2',
                                    label_visibility='collapsed') or views[0]
        if view == '섹터별 종목':
            _drilldown(m, res, labels, names, peak, jjin)
        else:
            _classification_editor(m, labels, names)
