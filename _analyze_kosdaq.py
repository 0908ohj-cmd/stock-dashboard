import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import sys, warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, r'C:\Users\PC\stock-dashboard')
from strategy.market_status import get_market_status
from strategy.rs_correction import calc_correction_rs
from strategy.indicators import calc_ma_position, calc_pct_from_52w_high

# ── 분석 기준일 ────────────────────────────────────────────
TARGET_DATE = pd.Timestamp('2026-05-21')
FETCH_END   = TARGET_DATE + timedelta(days=1)

# ── 티커 파싱 ──────────────────────────────────────────────
raw = "KRX:229200,KRX:KOSDAQ,###국폭,KRX:356680,KRX:078600,KRX:036540,KRX:252990,KRX:356860,KRX:092870,KRX:0015G0,KRX:290550,KRX:072950,KRX:077360,KRX:456010,KRX:046970,KRX:226950,KRX:440110,KRX:200470,KRX:327260,KRX:298380,KRX:124500,KRX:357880,KRX:039200,KRX:474610,KRX:290650,KRX:080580,KRX:089970,KRX:023160,KRX:319660,KRX:432720,KRX:098460,KRX:222800,KRX:218410,KRX:282720,KRX:019210,KRX:257720,KRX:389500,KRX:368770,KRX:376900,KRX:396300,KRX:083450,KRX:006910,KRX:037460,KRX:474170,KRX:089030,KRX:295310,KRX:263750,KRX:017510,KRX:080220,KRX:476060,KRX:466100,KRX:059120,KRX:347700,KRX:462350,KRX:083650,KRX:100790,KRX:056080,KRX:240810,KRX:067310,KRX:254490,KRX:458870,KRX:486990,KRX:059090,KRX:033790,KRX:042520,KRX:209640,KRX:036170,KRX:049630,KRX:031330,KRX:0015S0,KRX:010170,KRX:393890,KRX:388050,KRX:241520,KRX:094820,KRX:417840,KRX:490470,KRX:318060,KRX:065450,KRX:082270,KRX:147830,KRX:033160,KRX:452280,KRX:076610,KRX:046120,KRX:006730,KRX:138360,KRX:290690"
tickers = []
for t in raw.split(','):
    t = t.strip()
    if t.startswith('KRX:'):
        code = t[4:]
        if code.isdigit() or (len(code) == 7 and code[:6].isdigit()):
            tickers.append(code)

print(f"총 {len(tickers)}개 티커")

# ── KOSDAQ 지수 다운로드 ────────────────────────────────────
def download_df(yf_ticker, days=300):
    end   = FETCH_END
    start = end - timedelta(days=days)
    df = yf.download(yf_ticker, start=start, end=end, interval='1d',
                     progress=False, auto_adjust=True)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    cols = [c for c in ['Open','High','Low','Close','Volume'] if c in df.columns]
    return df[cols].dropna()

print("KOSDAQ 지수 다운로드 중...")
kosdaq_df = download_df('^KQ11')
if kosdaq_df.empty:
    print("KOSDAQ 데이터 없음")
    sys.exit(1)

# TARGET_DATE까지만 슬라이스
kosdaq_df = kosdaq_df[kosdaq_df.index <= TARGET_DATE]
print(f"KOSDAQ 데이터: {kosdaq_df.index[0].date()} ~ {kosdaq_df.index[-1].date()}, {len(kosdaq_df)}행")

# ── 시장 상태 확인 ─────────────────────────────────────────
status = get_market_status(kosdaq_df)
print(f"\n시장 상태: {status['state']}")
print(f"조정 시작: {status['correction_start'].date() if status['correction_start'] else 'N/A'}")
print(f"찐반등일:  {status['jjin_date'].date() if status['jjin_date'] else 'N/A'}")

correction_start = status['correction_start']
jjin_date = TARGET_DATE  # 사용자가 지정한 기준일

if correction_start is None:
    print("조정 구간 없음 — correction_start를 최근 21EMA 이탈일로 수동 설정 필요")
    sys.exit(1)

print(f"\n분석 구간: {correction_start.date()} ~ {jjin_date.date()}")

# ── 종목별 분석 ────────────────────────────────────────────
print(f"\n{len(tickers)}개 종목 분석 중...")
rows = []
failed = []

for i, ticker in enumerate(tickers):
    try:
        yf_ticker = ticker + '.KQ'
        df = download_df(yf_ticker)
        if df.empty or len(df) < 25:
            yf_ticker = ticker + '.KS'
            df = download_df(yf_ticker)
        if df.empty or len(df) < 25:
            failed.append(ticker)
            continue

        df = df[df.index <= TARGET_DATE]
        if len(df) < 10:
            failed.append(ticker)
            continue

        rs = calc_correction_rs(df, kosdaq_df, correction_start, jjin_date)

        close_on_target = float(df['Close'].iloc[-1])
        last_date = df.index[-1].date()

        rows.append({
            '티커':       ticker,
            '기준일종가': round(close_on_target, 0),
            '고점대비%':  calc_pct_from_52w_high(df),
            '저점선행(일)': rs['lead_days'],
            '조정RS%':    rs['excess_pct'],
            'MA점수':     rs['ma_score'],
            '거래량비%':  round(rs['vol_ratio'] * 100, 0),
            '양봉비%':    round(rs['candle_ratio'] * 100, 0),
        })
        if (i+1) % 20 == 0:
            print(f"  {i+1}/{len(tickers)} 완료...")
    except Exception as e:
        failed.append(ticker)

# ── 정렬 ───────────────────────────────────────────────────
rows.sort(key=lambda r: (
    -(r['조정RS%'] or 0),
    -r['MA점수'],
    -r['저점선행(일)'],
    -(r['거래량비%'] or 0),
))

# ── 출력 ───────────────────────────────────────────────────
print(f"\n{'='*90}")
print("코스닥 찐반등 스캐너  기준일: {}  조정시작: {}".format(jjin_date.date(), correction_start.date()))
print(f"{'='*90}")
print(f"{'순위':>4}  {'티커':>8}  {'기준일종가':>10}  {'고점대비%':>8}  {'조정RS%':>8}  {'MA점수':>6}  {'저점선행':>8}  {'거래량비%':>8}  {'양봉비%':>7}")
print(f"{'-'*95}")
for rank, r in enumerate(rows, 1):
    print(f"{rank:>4}  {r['티커']:>8}  {r['기준일종가']:>10.0f}  {r['고점대비%']:>8.1f}  {r['조정RS%']:>8.2f}  {r['MA점수']:>6}  {r['저점선행(일)']:>8}  {r['거래량비%']:>8.0f}  {r['양봉비%']:>7.0f}")

print(f"\n분석 성공: {len(rows)}개 / 실패: {len(failed)}개")
if failed:
    print(f"실패 티커: {failed}")
