#!/usr/bin/env python3
"""10EMA PP 셋업 알람 — GitHub Actions 배치 수집 후 실행.

셋업(케이스1/케이스2) 종목이 있으면 이메일 발송.
환경변수 GMAIL_APP_PASSWORD 없으면 조용히 종료.
"""
import os
import pathlib
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.store import load_daily, load_snapshot
from strategy.indicators import calc_adr
from strategy.pivot_candle import classify_case, find_pivot_candle

MARKETS = ['KR_KOSPI', 'KR_KOSDAQ', 'US']
MARKET_LABELS = {'KR_KOSPI': 'KOSPI', 'KR_KOSDAQ': 'KOSDAQ', 'US': 'NASDAQ'}
SETUP_STATES = {'셋업(케이스1)', '셋업(케이스2)'}
SENDER = '0908ohj@gmail.com'
RECEIVER = '0908ohj@gmail.com'


def _scan(market: str) -> list[dict]:
    snap = load_snapshot(market)
    results = []
    for ticker in snap.get('data', {}):
        try:
            df = load_daily(ticker, market)
            if df.empty or len(df) < 70:
                continue
            pivot = find_pivot_candle(df)
            state = classify_case(df, pivot)
            if state not in SETUP_STATES:
                continue
            current = float(df['Close'].iloc[-1])
            target = pivot['high']
            pct = (current / target - 1) * 100
            since = df[df.index > pivot['date']]
            adr = calc_adr(df)
            results.append({
                'ticker': ticker,
                'state': state,
                'target': target,
                'current': current,
                'pct': pct,
                'days': len(since),
                'adr': adr,
            })
        except Exception:
            continue
    results.sort(key=lambda r: abs(r['pct']))
    return results


def _build_html(all_setups: dict) -> str:
    rows = ''
    for market, setups in all_setups.items():
        label = MARKET_LABELS[market]
        for s in setups:
            color = '#e07000' if '케이스1' in s['state'] else '#1976d2'
            case_short = '케이스1' if '케이스1' in s['state'] else '케이스2'
            pct_color = '#c00' if s['pct'] < 0 else '#333'
            rows += (
                f'<tr>'
                f'<td style="padding:7px 12px">{label}</td>'
                f'<td style="padding:7px 12px;font-weight:700">{s["ticker"]}</td>'
                f'<td style="padding:7px 12px;color:{color};font-weight:600">{case_short}</td>'
                f'<td style="padding:7px 12px;text-align:right">{s["target"]:,.0f}</td>'
                f'<td style="padding:7px 12px;text-align:right;color:{pct_color}">{s["pct"]:+.1f}%</td>'
                f'<td style="padding:7px 12px;text-align:right">{s["days"]}일</td>'
                f'<td style="padding:7px 12px;text-align:right">{s["adr"]:.1f}%</td>'
                f'</tr>'
            )

    return f"""
<html><body style="font-family:sans-serif;color:#222;max-width:680px;margin:0 auto">
<h2 style="margin-bottom:4px">📊 10EMA PP 셋업 알람</h2>
<p style="color:#666;font-size:13px;margin-top:0">셋업 상태 종목 — 매일 장 마감 후 자동 발송</p>
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <thead>
    <tr style="background:#f4f4f4;border-bottom:2px solid #ddd">
      <th style="padding:8px 12px;text-align:left">시장</th>
      <th style="padding:8px 12px;text-align:left">티커</th>
      <th style="padding:8px 12px;text-align:left">케이스</th>
      <th style="padding:8px 12px;text-align:right">타점</th>
      <th style="padding:8px 12px;text-align:right">현재→타점%</th>
      <th style="padding:8px 12px;text-align:right">횡보일수</th>
      <th style="padding:8px 12px;text-align:right">ADR%</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</body></html>"""


def main() -> None:
    password = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not password:
        print('[notify] GMAIL_APP_PASSWORD 미설정 — 건너뜀')
        return

    all_setups: dict[str, list] = {}
    for market in MARKETS:
        setups = _scan(market)
        if setups:
            all_setups[market] = setups

    if not all_setups:
        print('[notify] 셋업 없음 — 이메일 미발송')
        return

    parts = [f"{MARKET_LABELS[m]} {len(v)}개" for m, v in all_setups.items()]
    subject = f"[PP셋업] {' / '.join(parts)}"

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SENDER
    msg['To'] = RECEIVER
    msg.attach(MIMEText(_build_html(all_setups), 'html', 'utf-8'))

    with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SENDER, password)
        smtp.sendmail(SENDER, RECEIVER, msg.as_string())

    total = sum(len(v) for v in all_setups.values())
    print(f'[notify] 이메일 발송 완료 — {total}개 셋업 ({subject})')


if __name__ == '__main__':
    main()
