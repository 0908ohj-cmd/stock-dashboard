"""주도섹터 탭 — 나스닥(210종목 고정 유니버스)·코스피·코스닥 섹터 강도 (마스터 명세 9장).

시세는 배치 스냅샷(data/ohlcv/*.json)만 읽는다. 스냅샷에 없는 종목은 MISSING으로
표시하고 실시간으로 받지 않는다 (명세 3장: 화면에서 일괄 다운로드 금지).
"""
import base64
import json
import pathlib
from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from data import classification as cls
from data import store
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
    bench: tuple             # 벤치마크 2개
    bench_snapshot: str      # 벤치마크 일봉이 있는 스냅샷
    ticker_files: tuple      # 유니버스(US는 고정 파일 사용)
    liquidity_shares: int | None
    liquidity_value: float | None
    value_unit: str          # 거래대금 표시 단위
    value_div: float


MARKETS = {
    'US': Market('US', '나스닥 기술·혁신 성장주 210종목', ('QQQ', 'IWM'), 'US', (),
                 1_000_000, None, '$M', 1e6),
    'KR_KOSPI': Market('KR_KOSPI', '코스피 와치리스트', ('KOSPI', 'KOSDAQ'), 'indices',
                       ('kospi.tickers', 'kospi_10ema.tickers'), None, 5e9, '억원', 1e8),
    'KR_KOSDAQ': Market('KR_KOSDAQ', '코스닥 와치리스트', ('KOSPI', 'KOSDAQ'), 'indices',
                        ('kosdaq.tickers', 'kosdaq_10ema.tickers'), None, 5e9, '억원', 1e8),
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
def _compute(market_key: str, asof: str, data_key: tuple) -> dict:
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
    rep = ss.run(bars, tickers, theme_of, main, asof, tax['version'], bench_names=m.bench,
                 liquidity_shares=m.liquidity_shares, liquidity_value=m.liquidity_value)
    return {
        'asof': rep.asof,
        'bench': {'names': list(rep.bench.names), 'ret5': rep.bench.ret5, 'ret21': rep.bench.ret21,
                  'blend5': rep.bench.blend5, 'blend21': rep.bench.blend21},
        'stocks': rep.stocks, 'sectors': rep.sectors, 'leaders': rep.leaders,
        'coverage': rep.coverage, 'taxonomyVersion': tax['version'],
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


# ── 섹션 ─────────────────────────────────────────────────

def _overview(m: Market, res: dict, labels: dict):
    b, cov = res['bench'], res['coverage']
    b1, b2 = b['names']
    sectors = [s for s in res['sectors'] if s['inMainRanking']]
    a = [s for s in sectors if s['grade'] == 'A_CONFIRMED']
    bb = [s for s in sectors if s['grade'] == 'B_EMERGING']
    scored = [s for s in sectors if s['score'] is not None]

    c = st.columns(6)
    c[0].metric('기준일', res['asof'])
    c[1].metric(f'{b1} 5일 / 21일', f"{b['ret5'][b1]:+.2f}% / {b['ret21'][b1]:+.2f}%")
    c[2].metric(f'{b2} 5일 / 21일', f"{b['ret5'][b2]:+.2f}% / {b['ret21'][b2]:+.2f}%")
    c[3].metric('데이터 확보', f"{cov['full']}/{cov['total']}")
    c[4].metric('근거 확인된 분류', f"{cov['classified']}/{cov['total']}")
    c[5].metric('A / B 섹터', f'{len(a)} / {len(bb)}')

    lines = []
    if not scored:
        lines.append('점수를 낼 수 있는 섹터가 없습니다. A가 없는 것이 아니라 **판정할 수 없는 상태**입니다.')
    else:
        best = max((s for s in scored if s['medianNrs5'] is not None), key=lambda s: s['medianNrs5'], default=None)
        lines.append(f"점수 산출 섹터 {len(scored)}개 중 A {len(a)}개, B {len(bb)}개."
                     + (f" A: {', '.join(labels[s['theme']] for s in a)}." if a else ' 확인된 주도섹터(A)는 없습니다.'))
        if best:
            lines.append(f"5일 ADR 보정 RS가 가장 강한 섹터는 **{labels[best['theme']]}** "
                         f"(중앙값 {best['medianNrs5']:+.2f}, 10EMA 위 {_pct(best['pctAbove10'])}).")
    weak_mkt = b['blend5'] < 0 and b['blend21'] < 0
    lines.append(f'시장({b1}·{b2} 평균)은 5일·21일 모두 하락 — 포지션 규모를 줄이는 환경입니다.' if weak_mkt
                 else f"시장({b1}·{b2} 평균) 5일 {b['blend5']:+.2f}%, 21일 {b['blend21']:+.2f}%.")
    unver = [s for s in sectors if s['grade'] == 'UNVERIFIED']
    if unver:
        lines.append(f"미검증 섹터 {len(unver)}개 (구성 5종목 미만 또는 데이터 80% 미만) — 아래 표에서 사유 확인.")
    st.info('\n\n'.join(lines))


def _sector_card(s: dict, labels: dict, leaders_by_theme: dict, names: dict, b1: str, b2: str):
    with st.container(border=True):
        top = st.columns([3, 1])
        top[0].markdown(f"**{labels[s['theme']]}** · {s['validCount']}/{s['memberCount']}종목")
        top[1].markdown(f"**{s['score']:.1f}점**")
        st.caption(
            f"5일 RS {_num(s['medianRs5'])}%p ({b1} {_num(s['medianRs5B1'])} · {b2} {_num(s['medianRs5B2'])}) · "
            f"21일 RS {_num(s['medianRs21'])}%p · RS/ADR 5일 {_num(s['medianNrs5'])} / 21일 {_num(s['medianNrs21'])}")
        st.caption(
            f"10EMA 위 {_pct(s['pctAbove10'])} (5일 전 대비 {_num(s['ema10ChangePp'], 0)}%p) · "
            f"3/5 유지 {_pct(s['pctEma10Persist3Of5'])} · 21EMA 위 {_pct(s['pctAbove21'])} · "
            f"양봉 RVOL≥1.5 {_pct(s['pctBullishRvol15_5D'])}")
        checks = s.get('gateChecks', {})
        if checks:
            st.caption('A 조건: ' + ' · '.join(('✅ ' if ok else '❌ ') + k for k, ok in checks['A'].items()))
        top3 = leaders_by_theme.get(s['theme'], [])[:3]
        if top3:
            st.caption('대표주: ' + ', '.join(
                f"{names.get(r['ticker'], r['ticker'])}({r['leaderScore']:.0f})" for r in top3))
        if 'SCORE_PRELIMINARY' in (s.get('flags') or []):
            st.caption('⚠️ 평가 가능 섹터가 4개 미만이라 백분위가 불안정합니다 (잠정 점수)')


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
    out = []
    for r in rows:
        row = {'티커': r['ticker']}
        if names:
            row['종목명'] = names.get(r['ticker'], '')
        row.update({
            '테마': labels.get(r.get('primaryTheme'), '미분류'),
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
    pick_theme = f[0].multiselect('테마', themes, key=f'{m.key}_lb_theme')
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
    b1, b2 = res['bench']['names']
    st.markdown(f"""
**수익률·RS** — `R_N = 100 × (C_t / C_(t−N) − 1)`, N = 5·21거래일. `RS = R_종목 − R_벤치`를 {b1}, {b2}, 둘의 평균에 대해 각각 계산 (%p).
섹터 수익률은 구성 종목 **중앙값** — 한 종목 급등이 섹터 전체를 끌어올리지 않습니다.

**ADR20 보정 RS** — `ADR20 = 최근 20일 평균 100 × (고가 − 저가) / 저가`, `RS/ADR = RS(평균 대비) / ADR20`. 섹터 값은 종목별 보정값의 중앙값. 5일과 21일은 서로 직접 비교하지 않고 각자 섹터 간 백분위로 점수화.

**추세** — 10EMA·21EMA(지수이동평균)와 50SMA(단순이동평균)를 각 거래일마다 따로 계산. "3/5 유지" = 최근 5거래일 중 3일 이상 그날의 10EMA 위 종가.

**거래량** — `RVOL20 = 당일 거래량 / 직전 20일 평균` (당일 제외). RVOL 확산 = 최근 5일 중 **양봉(종가 > 시가)**이면서 RVOL20 ≥ 1.5인 날이 있었던 종목 비율.

**섹터 100점** — 5일 RS 10 + 5일 RS/ADR 10 + 21일 RS 10 + 21일 RS/ADR 10 (섹터 간 백분위, 최하위 0·최상위 만점) + 10EMA 위 20 + 3/5 유지 15 + 21EMA 위 10 + RVOL 확산 10 + 5일 플러스 종목 5.

**판정 게이트** — 구성 5종목 이상, 데이터 확보 80% 이상이어야 점수를 냅니다. **A** = 75점 이상 + 5일·21일 섹터 수익률이 {b1}·{b2} 모두 초과 + 10EMA 위 60% + 3/5 유지 50% + 21EMA 위 40%. **B** = 60점 이상 + 5일 RS 양수 + (10EMA 위 40% 또는 5일 전 대비 +20%p).
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


def render_sector_leadership_tab(market_key: str = 'US'):
    m = MARKETS[market_key]
    st.caption(f"{m.title} · {'·'.join(m.bench)} 대비 5일/21일 RS, ADR20 보정 RS, 10EMA 유지력, 거래량 확산")
    if not _universe(m):
        st.info('와치리스트가 비어 있습니다. 사이드바에서 업로드해 주세요.')
        return
    opts = _asof_options(m)
    if not opts:
        st.warning(f"스냅샷에 {'·'.join(m.bench)} 일봉이 없습니다. 다음 배치 수집 후 표시됩니다.")
        return
    asof = st.selectbox('기준일 (과거 날짜를 고르면 그날 종가까지만 써서 재현)', opts, key=f'{m.key}_asof')
    try:
        res = _compute(m.key, asof, _data_key(m))
    except ValueError as e:
        st.error(f'계산할 수 없습니다: {e}')
        return
    labels, names = _labels(m), _names(m)
    b1, b2 = res['bench']['names']

    _overview(m, res, labels)
    main = [s for s in res['sectors'] if s['inMainRanking']]
    leaders_by_theme: dict = {}
    for r in sorted(res['leaders'], key=lambda r: -r['leaderScore']):
        leaders_by_theme.setdefault(r['primaryTheme'], []).append(r)

    has_score = any(s['score'] is not None for s in main)
    ca, cb = st.columns(2)
    for col, grade, title in ((ca, 'A_CONFIRMED', 'A 확인된 주도섹터'), (cb, 'B_EMERGING', 'B 차기 전환 후보')):
        with col:
            st.markdown(f'#### {title}')
            picked = [s for s in main if s['grade'] == grade]
            for s in picked:
                _sector_card(s, labels, leaders_by_theme, names, b1, b2)
            if not picked:
                st.caption('없음' if has_score else '판정 불가 (점수 산출 섹터 없음)')

    t = st.tabs(['섹터 표', '섹터 지도', '섹터 상세', '주도주 순위', '별도 테마', '분류 검토', '계산 방법·감사'])
    with t[0]:
        _sector_table(main, labels)
    with t[1]:
        _heatmap(m, main, labels)
    with t[2]:
        _drilldown(m, res, labels, names)
    with t[3]:
        _leaderboards(m, res, labels, names)
    with t[4]:
        st.caption('메인 랭킹에서 뺀 테마 (US: 바이오·금속·석유가스·소비, KR: 지주·기타)는 참고로만 보여줍니다.')
        _sector_table([s for s in res['sectors'] if not s['inMainRanking']], labels)
    with t[5]:
        _classification_editor(m, labels, names)
    with t[6]:
        _methodology(m, res)
        _downloads(m, res, labels, names)
