---
name: stock-dashboard
description: Stock Watchlist Dashboard 전담 에이전트. TradingView CSV 업로드 → DAY1/2/3 추세추종 전략 분석 → 한국/미국 와치리스트 표시하는 Streamlit 앱 작업 시 사용.
tools: Read, Edit, Write, Glob, Grep, Bash
model: sonnet
---

# Stock Watchlist Dashboard

TradingView 스크리너 CSV를 업로드하면 추세추종 전략(DAY1/2/3)을 자동 분석해 한국/미국 와치리스트를 보여주는 Streamlit 대시보드.

## 핵심 문서

- 스펙: `docs/superpowers/specs/2026-05-19-stock-watchlist-design.md`
- 플랜: `docs/superpowers/plans/2026-05-19-stock-watchlist.md`

## 현재 진행 상황 (2026-05-20 기준)

### 완료
- `requirements.txt`
- `data/fetcher.py` — yfinance(미국) + FinanceDataReader(한국) OHLCV 수집
- `data/sector.py`
- `strategy/indicators.py` — EMA21, SMA200, ADR, RS 계산
- `strategy/phases.py` — DAY1/DAY2/DAY3+ 페이즈 감지
- `strategy/scoring.py` — RS 상대강도 / 거래량 비대칭 / 양음봉 비율 스코어링
- `ui/index_panel.py` — KOSPI/KOSDAQ/SPY/QQQ 지수 현황 카드
- `ui/charts.py` — 일봉 + 5분봉 Plotly 차트
- `ui/watchlist.py` — 와치리스트 테이블 (탭: KOSPI/KOSDAQ/US)
- venv + 패키지 설치 완료

### 남은 작업
- `tests/test_indicators.py` — 테스트 코드는 플랜 파일에 있음
- `tests/test_phases.py` — 테스트 코드는 플랜 파일에 있음
- `tests/test_scoring.py` — 테스트 코드는 플랜 파일에 있음
- `app.py` — Streamlit 메인 진입점 (코드는 플랜 파일 Task 8에 있음)

## 트레이딩 전략 요약

### DAY1
최소 21EMA ~ 최대 200SMA 아래로 하락하는 봉 출현

### DAY2 (와치리스트 생성 조건)
DAY1 이후 동시 충족:
1. 거래량 급증 (평균 대비 1.5배 이상)
2. 상승폭 ≥ 지수 ADR
3. 양봉 크기 ≥ DAY1 봉 크기 × 0.8

### 스코어링 3기준 (0~3점)
1. 지수 대비 상대강도 (RS)
2. 거래량 비대칭 (상승일 거래량 > 하락일)
3. 양봉/음봉 비율

### DAY3~5
DAY2 저점 유지 or 고점 돌파 → 실제 매수 신호

## 기술 스택

- Python 3.11+, Streamlit, yfinance, FinanceDataReader, pandas, numpy, plotly
- 가상환경: `venv\Scripts\activate` 후 `streamlit run app.py`

## 파일 구조

```
stock-dashboard/
├── app.py                  # 메인 진입점 (미완성)
├── data/
│   ├── fetcher.py
│   └── sector.py
├── strategy/
│   ├── indicators.py
│   ├── phases.py
│   └── scoring.py
├── ui/
│   ├── index_panel.py
│   ├── watchlist.py
│   └── charts.py
├── tests/                  # 테스트 파일 미완성
├── requirements.txt
└── docs/superpowers/
    ├── specs/2026-05-19-stock-watchlist-design.md
    └── plans/2026-05-19-stock-watchlist.md
```
