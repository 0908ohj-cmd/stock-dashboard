# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

TradingView 스크리너에서 내보낸 CSV/TXT 티커 목록을 업로드하면, 추세추종 전략(지수 조정 → 찐반등 DAY1~5)과 10EMA 기준봉 전략으로 분석해 한국(KOSPI/KOSDAQ)·미국(NASDAQ) 와치리스트를 보여주는 Streamlit 대시보드.

- 배포: Streamlit Cloud (GitHub `0908ohj-cmd/stock-dashboard`, `runtime.txt` = python-3.11)
- UI 라벨·주석·커밋 메시지 모두 한국어. 커밋은 `feat:` / `fix:` / `docs:` / `refactor:` 접두사 사용.

## 명령어

```bash
pip install -r requirements.txt                            # 의존성 설치
streamlit run app.py                                       # 로컬 실행 (port 8501, .streamlit/config.toml)

python3 -m pytest tests/ --ignore=tests/test_scoring.py    # 전체 테스트
python3 -m pytest tests/test_phases.py -q                  # 파일 단위
python3 -m pytest tests/ -k "jjin" -q                      # 테스트 이름 패턴
```

- ⚠️ `tests/test_scoring.py`는 삭제된 `strategy/scoring.py`를 import하는 스테일 파일이라 이 파일 포함 시 전체 수집이 깨진다 — 반드시 `--ignore` 필요.
- 루트의 `실행.bat`·`주식대시보드_실행.bat`는 Windows PC용 런처 (이 Mac과 무관).

## 아키텍처

3계층 구조: `data/`(수집·파싱) → `strategy/`(순수 pandas 분석, Streamlit 무의존) → `ui/`(Streamlit + AgGrid + Plotly). `app.py`가 진입점.

### 데이터 흐름

1. 사이드바에서 CSV(TradingView 스크리너) 또는 TXT(TradingView 와치리스트) 업로드 → `data/fetcher.py`의 `parse_tradingview_csv` / `parse_ticker_txt`가 티커 추출 (`KRX:005930` → `005930`)
2. 티커는 `data/saved/*.tickers`에 저장. `st.secrets`에 `GITHUB_TOKEN`이 있으면 `app.py:_github_save`가 GitHub API로 repo에 자동 커밋 — Streamlit Cloud 재시작 후에도 티커가 유지되는 영속화 장치
3. 각 탭이 `fetch_daily`로 종목별 OHLCV 수집 → strategy 분석 → AgGrid 테이블 렌더

### 시세 수집 (`data/fetcher.py`) — KR 패치 체인이 핵심

yfinance가 1차 소스지만 한국 데이터 품질 문제(마지막 행 NaN, O=H=L=C 불완전 행, Volume=0)가 잦아서 **pykrx → FinanceDataReader 순 fallback 패치**(`_patch_kr_*` 함수들)를 거친다. KR 티커는 6자리 코드에 `.KS`/`.KQ` suffix를 붙여 조회하고 KOSPI 실패 시 `.KQ` 재시도. 종목명은 번들 `data/kr_names.json` → FDR → pykrx 순. KR 시세 관련 수정 시 이 패치 체인을 우회하지 말 것.

### 추세추종 전략 (코스피/코스닥/나스닥 탭 — `ui/watchlist.py`)

지수 상태 머신 `strategy/market_status.py:get_market_status`가 중심:

- `normal` → 지수 종가 EMA21 이탈 시 `correction`(DAY1) → 찐반등(장중 저가 EMA21 아래 + 양봉 상승폭 ≥ ADR + 직전 음봉 바디의 50% 이상 커버) 감지 시 `early_signal`(DAY2) → 이후 DAY3~5 (`strategy/phases.py`)
- 찐반등 실패 판정: 3거래일 내 EMA21 미회복 또는 찐반등 저점 아래 종가 → `correction` 복귀
- 종목 지표: 조정 구간 RS(`strategy/rs_correction.py` — 전고점~찐반등일 지수 대비 초과수익), 3종 RS(`strategy/indicators.py` — 초과수익률·RS Line 기울기·IBD식 등급), 스윙 등급(`strategy/swing_grade.py`)
- 스윙 등급: 사용자가 지정한 스윙로우 날짜들의 저가 시퀀스를 신고(3)/고(2)/저(1)/신저(0)로 레이블 → 최근 구간 가중 4진수 점수 → S / A++~B-- / C / F. 전이 제약 DFS(`_achievable_intermediate_scores`)로 달성 가능한 점수만 순위화
- ADR 필터: KR ≥ 2%, US ≥ 4%

### 10EMA 전략 (10EMA 탭 — `ui/watchlist_10ema.py` + `strategy/pivot_candle.py`)

기준봉(거래량 급증 + 60일 신고가 돌파 + 10>21>50 정배열 + 종가 상단 30% + 사전 30% 상승) 탐지 → 상태 분류(셋업/형성중/돌파완료/각종 이탈) → 타점(기준봉 고가)·손절(중간선)·리스크% 산출. ADR ≥ 6% 필터, ThreadPoolExecutor로 병렬 수집.

### 캐싱

- `st.cache_data(ttl=1800)` — 지수·와치리스트 행 빌드(`ui/watchlist.py`). 캐시 키로 쓰기 위해 날짜 등 인자를 문자열로 전달하는 패턴을 유지할 것
- 파일 기반: `sector_cache.json`(섹터), `data/kr_names.json`(KR 종목명 번들)
- `strategy/` 모듈은 Streamlit을 import하지 않는다(단위 테스트 가능성 유지). 캐시가 필요하면 `ui/`·`data/` 계층에서 래핑

### 알아둘 것

- `app.py` 상단의 세션 최초 1회 `st.rerun()`은 AgGrid 첫 탭 레이아웃 버그 우회 — 제거 금지
- 스펙·플랜 문서: `docs/superpowers/specs/`, `docs/superpowers/plans/`
- 전담 에이전트 정의: `.claude/agents/stock-dashboard.md` — 빠른 요약만 담고 있으며, 아키텍처·컨벤션은 이 문서(CLAUDE.md)가 기준
