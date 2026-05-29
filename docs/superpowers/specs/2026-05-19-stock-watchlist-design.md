# Stock Watchlist Dashboard — Design Spec
Date: 2026-05-19

## Overview

Streamlit 기반 주식 와치리스트 대시보드. 트레이딩뷰 스크리너 CSV를 업로드하면 추세추종 전략(DAY1/2/3 페이즈)을 자동 분석하고, 한국(KOSPI/KOSDAQ)과 미국 종목을 분리해 표시한다.

## Role Split

| 역할 | 담당 |
|------|------|
| 펀더멘털 필터링 (매출 성장률 등) | TradingView 스크리너 |
| 기술적 분석 + 와치리스트 생성 | 본 시스템 |

## Stock Universe

- 사용자가 TradingView 스크리너 결과를 CSV로 내보내기 → 대시보드에 업로드
- 한국 스크리너: 시가총액 ≥ 100B KRW, 90일 평균 거래량 > 300K, 분기 매출 성장률 YoY > 30%, 달러 거래대금 > 10B KRW, KOSPI+KOSDAQ
- 미국 스크리너: 시가총액 ≥ $300M, 90일 평균 거래량 > 1M, 분기 매출 성장률 YoY > 30%, 달러 거래대금 > $50M

## Trading Strategy (Ground Rule)

### DAY 1
최소 21EMA ~ 최대 200SMA 아래로 하락하는 봉 출현

### DAY 2 (와치리스트 생성 조건)
DAY1 이후 다음 3가지 동시 충족 시:
1. 거래량 급증 (평균 거래량 대비 유의미한 증가)
2. 상승폭 ≥ 지수 ADR
3. 양봉 크기 ≥ DAY1 봉 크기 (잡거나 버금가는)

### 와치리스트 스코어링 (3가지 기준)
1. **지수 대비 상대 강도**: 지수 하락 중 종목이 보합 or 상승 (일봉 + 5분봉)
2. **거래량 비대칭**: 하락 시 거래량 적고 + 상승 시 거래량 큼
3. **양봉 대 음봉 비율**: 최근 양봉이 직전 음봉 크기를 잡거나 버금가는지

### DAY 3~5 매수 신호
- DAY2 저점 유지 OR 고점 돌파 → 실제 매수 신호
- 신고가(종가) 매수 or 빗각 돌파 매수
- 프장에서 이미 1ADR 이상 오른 종목은 제외
- DAY2 와치리스트는 DAY3~5까지만 유효

### 매도 기준
- 손절: -1ADR 전량 매도
- 익절 1: +3ADR에서 33% 분할 매도
- 익절 2: 21EMA 아래로 뚫린 첫날 LoD 전량 매도
- 본전 방어: +1ADR 이후 본전선으로 이동

## Technical Indicators

| 지표 | 계산 |
|------|------|
| ADR | 최근 14일 (High-Low)/Close 평균 × 100% |
| RS | 종목 N일 수익률 / 지수 N일 수익률 (N=20) |
| EMA21 | 21일 지수이동평균 |
| SMA200 | 200일 단순이동평균 |

## Data Sources

- **미국 주식**: `yfinance` (15분 지연)
- **한국 주식**: `FinanceDataReader` (일봉 기준)
- **지수**: KOSPI (`^KS11`), KOSDAQ (`^KQ11`), SPY, QQQ

## Dashboard Layout

```
사이드바                    메인 화면
────────                   ──────────────────────
📁 CSV 업로드               [지수 현황]
🔄 새로고침                 KOSPI | KOSDAQ | SPY | QQQ
마켓 선택 (KR/US)          현재 DAY 페이즈 표시

                           [와치리스트]
                           탭: KOSPI | KOSDAQ | US
                           종목 | RS | 스코어 | DAY 신호

                           [종목 클릭 시]
                           → 일봉 차트 (EMA21/SMA200/볼륨)
                           → 5분봉 차트
                           → 스코어 세부 내역
```

## Tech Stack

- Python 3.11+
- `streamlit` — UI
- `yfinance` — 미국 주가 데이터
- `FinanceDataReader` — 한국 주가 데이터
- `pandas` / `numpy` — 데이터 처리
- `plotly` — 인터랙티브 차트

## Project Structure

```
stock-dashboard/
├── app.py                  # Streamlit 메인 진입점
├── data/
│   ├── fetcher.py          # yfinance/FDR 데이터 수집
│   └── cache.py            # 캐싱 레이어
├── strategy/
│   ├── indicators.py       # EMA, SMA, ADR, RS 계산
│   ├── phases.py           # DAY1/2/3 페이즈 감지
│   └── scoring.py          # 3가지 우세 기준 스코어링
├── ui/
│   ├── index_panel.py      # 지수 현황 패널
│   ├── watchlist.py        # 와치리스트 테이블
│   └── charts.py           # 일봉 + 5분봉 차트
├── requirements.txt
└── docs/
    └── superpowers/specs/
        └── 2026-05-19-stock-watchlist-design.md
```

## Validation

트레이딩뷰 스크리너 결과와 비교하면서 RS 계산, DAY 페이즈 감지 정확도를 점진적으로 개선한다.
