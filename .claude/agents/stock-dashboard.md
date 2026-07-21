---
name: stock-dashboard
description: Stock Watchlist Dashboard 전담 에이전트. TradingView CSV/TXT 업로드 → 추세추종(지수 조정→찐반등 DAY1~5)·10EMA 기준봉 전략 분석 → 한국/미국 와치리스트 표시하는 Streamlit 앱 작업 시 사용.
tools: Read, Edit, Write, Glob, Grep, Bash
model: sonnet
---

# Stock Watchlist Dashboard

TradingView 스크리너 CSV/와치리스트 TXT를 업로드하면 추세추종 전략(지수 조정 → 찐반등 DAY1~5)과 10EMA 기준봉 전략으로 분석해 KOSPI/KOSDAQ/NASDAQ 와치리스트를 보여주는 Streamlit 대시보드. Streamlit Cloud로 배포된다.

**아키텍처·명령어·컨벤션·함정은 루트 `CLAUDE.md`가 기준 문서다 — 작업 시작 전 반드시 읽을 것.** 이 파일에는 에이전트용 빠른 요약만 둔다.

## 핵심 문서

- 루트 `CLAUDE.md` — 아키텍처, 명령어, 컨벤션 (항상 최신 기준)
- 스펙: `docs/superpowers/specs/` (추세추종 와치리스트 / 찐반등 스캐너 / 10EMA 와치리스트)
- 플랜: `docs/superpowers/plans/`

## 구조 요약

3계층: `data/`(yfinance + pykrx/FDR 패치 체인 수집·파싱) → `strategy/`(순수 pandas, Streamlit 무의존) → `ui/`(Streamlit + AgGrid + Plotly). 진입점 `app.py`.

- 추세추종 탭: `ui/watchlist.py` + `strategy/market_status.py`(지수 상태 머신) · `phases.py`(DAY 레이블) · `rs_correction.py`(조정 구간 RS) · `swing_grade.py`(스윙 등급)
- 10EMA 탭: `ui/watchlist_10ema.py` + `strategy/pivot_candle.py`(기준봉 탐지·상태 분류·타점/손절)

## 전략 요약

### 추세추종 (DAY 체계)

지수 기준 상태 머신 — 종목이 아니라 **지수**가 페이즈를 결정:

- **DAY1** = 조정: 지수 종가가 EMA21 이탈 (`correction`)
- **DAY2** = 찐반등 감지 당일 (`early_signal`): 장중 저가 EMA21 아래 + 양봉 상승폭 ≥ ADR(20일) + 직전 음봉 바디 50% 이상 커버
- **DAY3~5** = 찐반등 이후 1~3거래일, 매수 유효 구간
- 실패 판정: 3거래일 내 EMA21 미회복 또는 찐반등 저점 아래 종가 → DAY1 복귀
- 종목 필터: ADR KR ≥ 2% / US ≥ 4%. 지표는 조정 구간 RS·3종 RS·스윙 등급(신고/고/저/신저 시퀀스 → S/A~B/C/F)

### 10EMA 기준봉

기준봉(거래량 급증 + 60일 신고가 돌파 + 10>21>50 정배열 + 종가 상단 30% + 사전 30% 상승) → 상태(셋업/형성중/돌파완료/각종 이탈) → 타점 = 기준봉 고가, 손절 = 중간선. ADR ≥ 6% 필터.

## 명령어

```bash
pip install -r requirements.txt
streamlit run app.py                                       # port 8501
python3 -m pytest tests/ --ignore=tests/test_scoring.py    # test_scoring.py는 스테일 — ignore 필수
```

## 주의사항

- KR 시세는 `data/fetcher.py`의 pykrx → FDR 패치 체인을 반드시 경유 (yfinance KR 데이터 품질 문제 보정)
- `app.py` 상단 최초 1회 `st.rerun()`은 AgGrid 레이아웃 버그 우회 — 제거 금지
- `strategy/`에 Streamlit import 금지 (단위 테스트 가능성 유지)
- UI 라벨·주석·커밋 메시지 한국어, 커밋 접두사 `feat:`/`fix:`/`docs:`/`refactor:`
