import streamlit as st
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

from strategy.trading_days import trading_days_after
from data.fetcher import get_stock_name
from data.store import load_daily, get_freshness, refetch_market
from data.sector import get_sectors, get_display_sectors
from data.yf_sector import get_yf_sectors
from data import leaderboard_store
from strategy.indicators import calc_pct_from_52w_high, calc_ema
from strategy.pivot_candle import find_pivot_candle, classify_case, calc_10ema_slope, pp_prefilter
from ui import tv_export
from ui.sector_leadership import hot_history, hot_badge

# 정렬: 케이스1 → 케이스2 → 이탈류 → 없음
# 케이스1 내부: 현재→타점%(10EMA 거리) 오름차순 — 가까울수록 위
STATE_ORDER = {
    '셋업(케이스1)': 0,
    '셋업(케이스2)': 1,
    '돌파완료':      2,
    '10EMA이탈':     3,
    '중간선이탈':    4,
    '저가이탈':      5,
    '없음':          6,
}

STATE_BADGE = {
    '셋업(케이스1)': '🟠 셋업(케이스1)',
    '셋업(케이스2)': '🟢 셋업(케이스2)',
    '돌파완료':      '🔵 돌파완료',
    '10EMA이탈':     '🔴 10EMA이탈',
    '중간선이탈':    '🔴 중간선이탈',
    '저가이탈':      '🔴 저가이탈',
    '없음':          '🔴 없음',
}

# UI 시장 코드 → 리더보드 시장 코드
_LB_MARKET = {'KR_KOSPI': 'kr', 'KR_KOSDAQ': 'kr', 'US': 'us'}

KO_LOCALE = {
    'searchOoo': '검색...', 'selectAll': '(모두 선택)',
    'noMatches': '일치 없음', 'filterOoo': '필터...',
    'sortAscending': '오름차순', 'sortDescending': '내림차순',
    'columns': '컬럼', 'filters': '필터',
}


def _ma_score(df: pd.DataFrame, close: float) -> int:
    mas = [
        calc_ema(df, 10).iloc[-1],
        calc_ema(df, 21).iloc[-1],
        df['Close'].rolling(50).mean().iloc[-1],
        df['Close'].rolling(150).mean().iloc[-1],
        df['Close'].rolling(200).mean().iloc[-1],
    ]
    return sum(1 for v in mas if not pd.isna(v) and close > v)


def _process_one(ticker: str, market: str) -> dict | None:
    try:
        df = load_daily(ticker, market=market)
        # ADR 6%↑ · 10EMA>21EMA>50SMA 정배열 · 종가 150·200SMA 위
        pre = pp_prefilter(df)
        if pre is None:
            return None
        adr, last_close, ema10_v = pre['adr'], pre['close'], pre['ema10']

        pivot  = find_pivot_candle(df)
        state  = classify_case(df, pivot)
        name   = get_stock_name(ticker, market)

        prev_close = float(df['Close'].iloc[-2])
        change_pct = (last_close - prev_close) / prev_close * 100

        pivot_date_str = str(pivot['date'].date()) if pivot else ''
        pivot_vol_r    = pivot['vol_ratio'] if pivot else 0.0
        days_since     = trading_days_after(df, pivot['date']) if pivot else 0

        if pivot:
            pivot_pos  = df.index.get_loc(pivot['date'])
            start_pos  = max(0, pivot_pos - 65)
            prior_low  = float(df['Low'].iloc[start_pos:pivot_pos].min()) if pivot_pos > 0 else 0.0
            prior_move_pct = round((pivot['high'] / prior_low - 1) * 100, 0) if prior_low > 0 else 0.0
            if state == '셋업(케이스1)':
                # 타점=10EMA, 손절=10EMA-0.5ADR
                entry_price    = round(ema10_v, 2)
                stop_price     = round(ema10_v * (1 - adr * 0.5 / 100), 2)
                risk_pct       = round(adr * 0.5, 1)
                near_entry_pct = round((last_close / entry_price - 1) * 100, 2)
            else:
                # 케이스2: 타점=기준봉 고가, 손절=기준봉 저가 -2%
                entry_price    = round(pivot['high'], 2)
                stop_price     = round(pivot['low'] * 0.98, 2)
                risk_pct       = round((entry_price - stop_price) / entry_price * 100, 1)
                near_entry_pct = round((last_close / entry_price - 1) * 100, 2)
        else:
            entry_price = stop_price = risk_pct = near_entry_pct = prior_move_pct = 0.0
        aligned3 = True  # _process_one 진입 시점에 이미 정배열 체크 완료

        return {
            'Ticker':        ticker,
            '종목명':        name,
            '상태':          state,
            'Close':         round(last_close, 2),
            '타점':          entry_price,
            '현재→타점%':    near_entry_pct,
            '손절':          stop_price,
            '리스크%':       risk_pct,
            '이전상승%':     prior_move_pct,
            '등락%':         round(change_pct, 2),
            '횡보일수':      days_since,
            '기준봉거래량비': round(pivot_vol_r, 1),
            'MA점수':        _ma_score(df, last_close),
            '고점대비%':     calc_pct_from_52w_high(df),
            '기준봉일':      pivot_date_str,
            'ADR%':          round(adr, 1),
            '정배열3':       aligned3,
        }
    except Exception:
        return None


_ROW_SCHEMA_VER = 23  # 컬럼 구조 변경 시 증가 → 구캐시 자동 무효화

@st.cache_data(ttl=3600)
def _build_10ema_rows(tickers_tuple: tuple, market: str, schema_ver: int = _ROW_SCHEMA_VER) -> list:
    rows = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_process_one, t, market): t for t in tickers_tuple}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                rows.append(result)

    # 섹터·테마는 수집 완료 후 배치 1회 부착 (워커 안에서 부르면 종목마다 분류가 뜬다)
    tickers = [r['Ticker'] for r in rows]
    themes = get_sectors(tickers, market)
    sectors = get_display_sectors(tickers, market, get_yf_sectors(tickers, market))
    for r in rows:
        t = r['Ticker']
        # 섹터 = 주력 테마(주도섹터 탭과 같은 기준). 승인된 분류가 없으면 yfinance로 메운다
        r['섹터'] = sectors.get(t, '-')
        r['테마'] = themes.get(t, '기타')

    rows.sort(key=lambda r: (
        STATE_ORDER.get(r['상태'], 99),
        -(r['현재→타점%'] or -99),   # 셋업 내: 타점에 가까울수록 먼저 (0에 가까운 순)
        -(r['이전상승%'] or 0),
    ))
    return rows


def render_10ema_tab(market: str, label: str):
    from data.universe import get_kr_10ema_universe, get_us_10ema_universe

    # 스캔 전에 비워둔다 — 유니버스 로딩 실패 등으로 빠져나가도 직전 실행에서 남은
    # 셋업 목록이 세션에 살아남아 파일에 실리는 일이 없어야 한다
    tv_export.register_ema10(market, [])

    with st.spinner(f'{label} 유니버스 로딩 중...'):
        tickers = get_us_10ema_universe() if market == 'US' else get_kr_10ema_universe(market)

    if not tickers:
        st.warning('유니버스를 불러오지 못했습니다. 잠시 후 새로고침 해주세요.')
        return

    with st.expander('사용 가이드', expanded=False):
        st.caption('10EMA (PP) 시장 최소 조건')
        st.markdown("""
<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px">
  <div style="border:1px solid #3498db55;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">📈 지수 조건</div>
    <div style="font-size:0.85em;line-height:1.8">10EMA &gt; 21EMA<br>10EMA · 21EMA 기울기 모두 양수</div>
    <div style="color:#3498db;font-size:0.82em;margin-top:8px">→ 지수 현황에서 거래 가능 여부 확인</div>
  </div>
  <div style="border:1px solid #9b59b655;border-radius:8px;padding:12px 14px">
    <div style="font-weight:700;margin-bottom:6px">📐 개별주 조건</div>
    <div style="font-size:0.85em;line-height:1.8">10EMA &gt; 21EMA &gt; 50SMA 정배열<br>종가 &gt; 150SMA · 200SMA<br>(기준봉 탐지 조건에 내장)</div>
    <div style="color:#9b59b6;font-size:0.82em;margin-top:8px">→ 조건 미충족 종목은 스캔에서 자동 제외</div>
  </div>
</div>
""", unsafe_allow_html=True)
        st.divider()
        st.caption('종목 분류 & 셋업 가이드 — 기준봉 이후 단 2거래일로 케이스 확정, 이후 절대 전환 없음')
        st.markdown(
            '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:18px">'

            '<div style="background:linear-gradient(135deg,#ff8c00,#e65c00);color:#fff;'
            'border-radius:20px;padding:7px 18px;font-weight:700;font-size:0.86em;'
            'box-shadow:0 3px 10px rgba(230,90,0,0.45);letter-spacing:0.03em">📊 기준봉</div>'

            '<div style="color:#e67800;font-size:1.5em;line-height:1;font-weight:700">➔</div>'

            '<div style="border:2px solid #e67800;border-radius:20px;padding:6px 18px;'
            'font-size:0.85em;background:rgba(230,120,0,0.1)">'
            '<b style="color:#e67800">D+1</b>&nbsp;종가 vs 기준봉 고가</div>'

            '<div style="color:#e67800;font-size:1.5em;line-height:1;font-weight:700">➔</div>'

            '<div style="border:2px solid #e67800;border-radius:20px;padding:6px 18px;'
            'font-size:0.85em;background:rgba(230,120,0,0.1)">'
            '<b style="color:#e67800">D+2</b>&nbsp;종가 vs 기준봉 고가</div>'

            '<div style="color:#aaa;font-size:1.5em;line-height:1;font-weight:700">➔</div>'

            '<div style="background:linear-gradient(135deg,#1a6b3c,#0d4a2a);color:#fff;'
            'border-radius:20px;padding:7px 18px;font-size:0.86em;font-weight:700;'
            'box-shadow:0 3px 10px rgba(20,100,50,0.4);letter-spacing:0.03em">'
            '🔒 케이스 확정 (이후 불변)</div>'

            '</div>',
            unsafe_allow_html=True,
        )
        _mc1, _mc2 = st.columns(2)
        _mc1.markdown(
            '<div style="border-radius:10px;overflow:hidden;border:1px solid rgba(230,120,0,0.3);'
            'min-height:420px;display:flex;flex-direction:column">'
            '<div style="background:linear-gradient(90deg,#ff8c00,#cc5500);padding:10px 16px">'
            '<span style="font-weight:700;color:#fff;font-size:0.92em">🟠 케이스1 — 10EMA 풀백</span>'
            '</div>'
            '<div style="padding:14px 16px;flex:1;display:flex;flex-direction:column">'

            '<div style="font-size:0.75em;color:#e67800;font-weight:700;letter-spacing:0.07em;margin-bottom:5px">▸ 확정 조건</div>'
            '<div style="font-size:0.84em;line-height:1.85;margin-bottom:12px">'
            'D+1 종가 &#62; 기준봉 고가<br>'
            'D+2 종가 &#62; 기준봉 고가</div>'

            '<div style="border-top:1px solid rgba(255,255,255,0.08);margin-bottom:12px"></div>'

            '<div style="font-size:0.75em;color:#e67800;font-weight:700;letter-spacing:0.07em;margin-bottom:5px">▸ 진입 전략</div>'
            '<div style="font-size:0.83em;margin-bottom:8px">'
            '<b>타점</b> = 10EMA &nbsp;·&nbsp; <b>손절</b> = 10EMA − 0.5×ADR</div>'
            '<div style="font-size:0.82em;line-height:2.0;margin-bottom:0">'
            '<b style="color:#e67800">①</b> 현재→타점% ±3% 이내 시 관심<br>'
            '<b style="color:#e67800">②</b> 장중 10EMA 터치 확인<br>'
            '<b style="color:#e67800">③</b> 5분봉 양봉 기준봉 형성 시 진입</div>'

            '<div style="border-top:1px solid rgba(255,255,255,0.08);margin:12px 0 10px;margin-top:auto"></div>'

            '<div style="font-size:0.82em;background:rgba(52,152,219,0.12);border-radius:6px;padding:7px 10px">'
            '🔵 <b>돌파완료</b>: 10EMA 위 종가 누적 10거래일 이상 (연속 아님)</div>'

            '</div></div>',
            unsafe_allow_html=True,
        )
        _mc2.markdown(
            '<div style="border-radius:10px;overflow:hidden;border:1px solid rgba(46,204,113,0.3);'
            'min-height:420px;display:flex;flex-direction:column">'
            '<div style="background:linear-gradient(90deg,#27ae60,#1a7a43);padding:10px 16px">'
            '<span style="font-weight:700;color:#fff;font-size:0.92em">🟢 케이스2 — 브레이크아웃</span>'
            '</div>'
            '<div style="padding:14px 16px;flex:1;display:flex;flex-direction:column">'

            '<div style="font-size:0.75em;color:#2ecc71;font-weight:700;letter-spacing:0.07em;margin-bottom:5px">▸ 확정 조건</div>'
            '<div style="font-size:0.84em;line-height:1.85;margin-bottom:6px">'
            'D+1 종가 ≤ 기준봉 고가 → 즉시 확정<br>'
            'D+1 &#62; 고가 + D+2 ≤ 고가 → 전환 확정</div>'
            '<div style="font-size:0.79em;background:rgba(231,76,60,0.1);'
            'border-left:3px solid #e74c3c;padding:5px 9px;margin-bottom:12px;border-radius:0 5px 5px 0">'
            '⚡ D+1엔 케이스1 잠정 표시 → D+2 고가 아래 마감 시 케이스2 전환 (전환의 유일한 순간)</div>'

            '<div style="border-top:1px solid rgba(255,255,255,0.08);margin-bottom:12px"></div>'

            '<div style="font-size:0.75em;color:#2ecc71;font-weight:700;letter-spacing:0.07em;margin-bottom:5px">▸ 진입 전략</div>'
            '<div style="font-size:0.83em;margin-bottom:8px">'
            '<b>타점</b> = 기준봉 고가 &nbsp;·&nbsp; <b>손절</b> = 기준봉 저가 − 2%</div>'
            '<div style="font-size:0.82em;line-height:2.0;margin-bottom:0">'
            '<b style="color:#2ecc71">①</b> 현재→타점% 5% 이내 시 관심<br>'
            '<b style="color:#2ecc71">②</b> 렐볼 급증 + 기준봉 고가 돌파 확인<br>'
            '<b style="color:#2ecc71">③</b> 5분봉 기준봉 형성 시 진입</div>'

            '<div style="border-top:1px solid rgba(255,255,255,0.08);margin:12px 0 10px;margin-top:auto"></div>'

            '<div style="font-size:0.82em;background:rgba(52,152,219,0.12);border-radius:6px;padding:7px 10px">'
            '🔵 <b>돌파완료</b>: 케이스2 확정 이후 이틀 연속 기준봉 고가 위 종가</div>'

            '</div></div>',
            unsafe_allow_html=True,
        )
        st.write('')
        _it1, _it2, _it3, _it4 = st.columns(4)
        for _col, _title, _body in [
            (_it1, '🔴 10EMA이탈',  '10EMA 아래 연속 2거래일'),
            (_it2, '🔴 중간선이탈', '기준봉 중간선 아래 연속 2거래일'),
            (_it3, '🔴 저가이탈',   '기준봉 저가 하방 이탈'),
            (_it4, '⚫ 없음',        '최근 6개월 내 기준봉 미탐지'),
        ]:
            _col.markdown(
                '<div style="border-radius:10px;overflow:hidden;border:1px solid rgba(180,50,50,0.25)">'
                '<div style="background:linear-gradient(90deg,#922b21,#641a14);padding:7px 12px">'
                f'<span style="font-weight:700;color:#fff;font-size:0.81em">{_title}</span>'
                '</div>'
                f'<div style="padding:10px 12px;font-size:0.82em;line-height:1.7">{_body}<br>'
                '<span style="color:#aaa;font-size:0.9em">→ 셋업 무효</span></div>'
                '</div>',
                unsafe_allow_html=True,
            )

        st.divider()
        st.caption('기준봉이란? — 탐지 조건 4가지를 모두 충족해야 유효')
        _p1, _p2, _p3, _p4 = st.columns(4)
        for _col, _num, _title, _desc in [
            (_p1, '①', '거래량 손바뀜',  '평소 대비 <b>2배+</b> 폭발 — 물린 매물이 새 매수자로 교체되는 손바뀜 발생'),
            (_p2, '②', '저항 돌파',      '<b>60일 신고가</b> 또는 VCP 박스 상단 돌파. 52주 신고가 돌파 시 오버행 없어 가장 강력'),
            (_p3, '③', '종가 위치',      '당일 레인지 <b>상단 70%+</b> 마감 — 윗꼬리 없이 강하게 닫혀야 유효'),
            (_p4, '④', '이평선 정배열',  '10EMA &#62; 21EMA &#62; 50MA 정배열 + 장기 이평선(150/200) 위 종가'),
        ]:
            _col.markdown(
                '<div style="border-radius:10px;overflow:hidden;border:1px solid rgba(30,80,140,0.35)">'
                '<div style="background:linear-gradient(90deg,#1e4d7b,#0f2d4a);padding:8px 12px">'
                f'<span style="font-weight:700;color:#fff;font-size:0.83em">{_num} {_title}</span>'
                '</div>'
                f'<div style="padding:11px 13px;font-size:0.82em;line-height:1.75">{_desc}</div>'
                '</div>',
                unsafe_allow_html=True,
            )

        st.divider()
        st.caption('컬럼 설명')
        st.markdown(
            '| 컬럼 | 설명 | 기준 |\n'
            '|------|------|------|\n'
            '| 기준봉일 | 기준봉 발생 날짜 | |\n'
            '| 타점 | 기준봉 고가 — ORH 매수 진입가 | |\n'
            '| 현재→타점% | 현재가 대비 타점까지 남은 거리 (음수 = 타점 아래 = 진입 가능) | −5% 이내 |\n'
            '| 횡보일수 | 기준봉 이후 경과 거래일 수 | 3 ~ 40일 |\n'
            '| 이전상승% | 기준봉 전 65거래일 저점 → 고가 상승폭 (Prior Move) | 30%+ 필수 |\n'
            '| ADR% | 최근 20일 평균 일일 변동폭 | 6%+ 필터 |\n'
        )

        st.divider()
        if st.button('🔄 재스캔', key=f'rescan_10ema_{market}', help='전 종목 재스캔 — 수 분 소요'):
            with st.spinner(f'{label} 시세 재수집 중... ({len(tickers)}개 종목)'):
                refetch_market(market, tickers)
            _build_10ema_rows.clear()
            st.rerun()

    fr = get_freshness(market)
    if fr['fetched_at']:
        st.caption(
            f"📅 {fr['last_trading_date']} 장마감 기준 · "
            f"수집 {fr['fetched_at'].strftime('%m-%d %H:%M')}"
        )

    with st.spinner(f'{label} 스캔 중... {len(tickers)}개 종목 (첫 로드 시 수 분 소요)'):
        rows = _build_10ema_rows(tuple(sorted(tickers)), market, _ROW_SCHEMA_VER)

    if not rows:
        st.warning('분석 가능한 종목이 없습니다.')
        return

    # 상단 요약 메트릭
    case1_rows  = [r for r in rows if r['상태'] == '셋업(케이스1)']
    case2_rows  = [r for r in rows if r['상태'] == '셋업(케이스2)']
    near_rows   = [r for r in case2_rows if abs(r['현재→타점%'] or 99) <= 5]
    entry_rows  = case1_rows + case2_rows

    # 2026-11-11 사용자 요청으로 10EMA 섹션은 TradingView 내보내기에서 뺐다 (맨 위에서 빈 목록 등록만 유지).
    # 다시 넣으려면: tv_export.register_ema10(market, [r['Ticker'] for r in entry_rows])
    m1, m2, m3, m4 = st.columns(4)
    ital_cnt = len([r for r in rows if r['상태'] not in ('셋업(케이스1)', '셋업(케이스2)')])
    m1.metric('🟠 케이스1', f'{len(case1_rows)}개')
    m2.metric('🟢 케이스2', f'{len(case2_rows)}개')
    m3.metric('📊 스캔', f'{len(tickers)}개')
    m4.metric('🔴 이탈', f'{ital_cnt}개')

    # 표시 필터
    show_failed = st.checkbox('🔴 이탈/돌파완료 보기', value=False, key=f'show_failed_{market}')

    display_rows = rows if show_failed else entry_rows

    if not display_rows:
        st.info('현재 셋업 종목 없음.')
        return

    crown = leaderboard_store.get_tickers(_LB_MARKET.get(market, 'us'))
    # 🔥 돈 몰린 섹터(지금 또는 최근 6주) 소속이면 배지 — 주도섹터 탭 순환매와 같은 계산
    try:
        hot, theme_of, hot_labels = hot_history(market)
    except Exception:
        hot, theme_of, hot_labels = {}, {}, {}

    def _hot(t):
        th = theme_of.get(t)
        return hot_badge(hot[th], hot_labels.get(th, th)) if th in hot else ''

    display_df = pd.DataFrame([{
        # 배지는 별도 컬럼 — 티커 문자열에 섞으면 정렬·필터가 그 접두사까지 보게 된다
        '👑': '👑' if r['Ticker'] in crown else '',
        '티커 | 종목명':  f"{r['Ticker']} | {r['종목명']}",
        '🔥 섹터':        _hot(r['Ticker']),
        '상태':           STATE_BADGE.get(r['상태'], r['상태']),
        '섹터':           r['섹터'],
        '테마':           r['테마'],
        'Close':          r['Close'],
        '등락%':          r['등락%'],
        '기준봉일':       r['기준봉일'],
        '타점':           r['타점'],
        '현재→타점%':     r['현재→타점%'],
        '횡보일수':       r['횡보일수'],
        '이전상승%':      r['이전상승%'],
        'ADR%':           r['ADR%'],
    } for r in display_rows])

    if market.startswith('KR'):
        price_fmt = "value == null ? '' : '₩' + Math.round(value).toLocaleString('ko-KR')"
    else:
        price_fmt = "value == null ? '' : '$' + value.toFixed(2)"

    gb = GridOptionsBuilder.from_dataframe(display_df)
    gb.configure_default_column(sortable=True, resizable=True, filter=True, floatingFilter=True, flex=1)
    # 리더보드 배지 — 티커와 함께 왼쪽 고정(안 하면 고정 컬럼 뒤로 밀린다)
    gb.configure_column('👑', headerName='👑', headerTooltip='리더보드 편입 종목',
                        sortable=True, filter=False, floatingFilter=False,
                        resizable=False, suppressSizeToFit=True, pinned='left', flex=0,
                        width=44, minWidth=40, maxWidth=52)
    gb.configure_column('티커 | 종목명', filter='agTextColumnFilter', pinned='left', minWidth=170, flex=2)
    gb.configure_column('🔥 섹터', filter='agTextColumnFilter', minWidth=130, flex=1,
                        headerTooltip='지금 또는 최근 6주 안에 🔥 급부상했던 섹터 소속 — 괄호는 🔥 시작일. 주도섹터 탭 🔥 급부상 섹터와 같은 계산')
    gb.configure_column('상태',  filter='agSetColumnFilter', minWidth=120, flex=1)
    gb.configure_column('섹터', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('테마', filter='agSetColumnFilter', minWidth=110, flex=1)
    gb.configure_column('Close', filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=price_fmt, flex=1)
    gb.configure_column('등락%', filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_column('타점',  filter='agNumberColumnFilter', type=['numericColumn'], valueFormatter=price_fmt, flex=1)
    gb.configure_column('기준봉일', filter='agTextColumnFilter', flex=1)
    for col in ['현재→타점%', '이전상승%', '횡보일수', 'ADR%']:
        gb.configure_column(col, filter='agNumberColumnFilter', type=['numericColumn'], flex=1)
    gb.configure_grid_options(rowHeight=28, localeText=KO_LOCALE)

    grid_height = 72 + len(display_df) * 28

    AgGrid(
        display_df,
        gridOptions=gb.build(),
        update_mode=GridUpdateMode.NO_UPDATE,
        enable_enterprise_modules=False,
        theme='streamlit',
        height=grid_height,
        fit_columns_on_grid_load=True,
    )

    def fmt_price(v):
        if market == 'US':
            return f"\\${v:,.2f}"  # \$ → Streamlit 마크다운 LaTeX 파싱 방지
        return f"₩{v:,.0f}"

    # 셋업(케이스1) 배너 — 10EMA 3% 이내 ⚡, 그 외 📌
    if case1_rows:
        lines = []
        for r in case1_rows:
            pct = r['현재→타점%'] or 0
            flag = '⚡' if abs(pct) <= 3 else '📌'
            price_str = fmt_price(r['타점'])
            lines.append(
                f"{flag} **{r['Ticker']}** {r['종목명']} | "
                f"{r['기준봉일']} | "
                f"타점 {price_str} | "
                f"현재→타점 **{pct:+.1f}%** | ADR {r['ADR%']}%"
            )
        st.warning('🟠 **셋업(케이스1)** — 10EMA 풀백 진입 (⚡ = 10EMA ±3% 이내)  \n' + '  \n'.join(lines), icon=None)

    # 셋업(케이스2) 배너 — 타점 5% 이내 ⚡, 그 외 📌
    if case2_rows:
        lines = []
        for r in case2_rows:
            pct = r['현재→타점%'] or 0
            flag = '⚡' if abs(pct) <= 5 else '📌'
            price_str = fmt_price(r['타점'])
            lines.append(
                f"{flag} **{r['Ticker']}** {r['종목명']} | "
                f"{r['기준봉일']} | "
                f"타점 {price_str} | "
                f"현재→타점 **{pct:+.1f}%** | ADR {r['ADR%']}%"
            )
        st.success('🟢 **셋업(케이스2)** — 브레이크아웃 대기 (⚡ = 타점 5% 이내)  \n' + '  \n'.join(lines), icon=None)
