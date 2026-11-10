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

- `normal` → 지수 종가 EMA21 이탈 시 `correction`(DAY1) → 찐반등(장중 저가 EMA21 아래 + 양봉 + 상승폭 ≥ ADR×0.9) 감지 시 `early_signal`(DAY2) → 이후 DAY3~5 (`strategy/phases.py`)
- 찐반등 실패 판정: 5거래일 내 EMA21 미회복(6번째 거래일에 확정) 또는 찐반등 저점 아래 종가(당일 즉시) → `correction` 복귀. 실패한 후보의 **다음 거래일부터 새 찐반등을 다시 탐색**하므로, 실패가 이후의 유효한 반등을 가리지 않는다
- 종목 지표: 조정 구간 RS(`strategy/rs_correction.py` — 전고점~찐반등일 지수 대비 초과수익), 3종 RS(`strategy/indicators.py` — 초과수익률·RS Line 기울기·IBD식 등급), 스윙 등급(`strategy/swing_grade.py`)
- 스윙 등급: 사용자가 지정한 스윙로우 날짜들의 저가 시퀀스를 신고(3)/고(2)/저(1)/신저(0)로 레이블 → 최근 구간 가중 4진수 점수 → S / A++~B-- / C / F. 전이 제약 DFS(`_achievable_intermediate_scores`)로 달성 가능한 점수만 순위화
- ADR 필터: KR ≥ 2%, US ≥ 4%
- 매수 후보 필터: RS/ADR > 0 · 이평선위치 · 52주 고점대비 ≥ −30% (없으면 −35%로 완화한 상위 5개). **거래량비% 필터는 2026-11-09 제거**
- **세 화면 일관성 원칙** (추세추종 매수 후보 · 차기 주도주 · 순환매): 같은 개념은 같은 정의로 쓰고, 2026-11-09 5년 백테스트에서 효과 없음/역효과인 재료는 세 곳 모두에서 뺀다 — 거래량비(상승일/하락일 거래량, 역효과), 21EMA 위(역효과·중복), RS 가속(효과 없음). 효과가 확인된 52주 고점 근접은 공통 기준. 재료를 추가·제거할 땐 세 곳을 함께 바꿀 것

### 10EMA 전략 (10EMA 탭 — `ui/watchlist_10ema.py` + `strategy/pivot_candle.py`)

기준봉(거래량 급증 + 60일 신고가 돌파 + 10>21>50 정배열 + 종가 상단 30% + 사전 30% 상승) 탐지 → 상태 분류(셋업/형성중/돌파완료/각종 이탈) → 타점(기준봉 고가)·손절(중간선)·리스크% 산출. ADR ≥ 6% 필터, ThreadPoolExecutor로 병렬 수집.

### 🇺🇸 주도섹터 탭 (`ui/sector_leadership.py` + `strategy/sector_strength.py`)

미국 기술·혁신 성장주 210종목(`data/universe/us_innovation_210.json`, 원본 워치리스트 고정 멤버십)의 섹터 강도. 설계 기준은 사용자 마스터 명세(2026-11-08).

- 시세는 US 스냅샷만 읽는다. 210종목 + QQQ·IWM은 `data/saved/us_sector.tickers`로 US 배치에 포함된다. 화면에서 실시간 다운로드 금지, 스냅샷에 없으면 MISSING
- 벤치마크는 시장마다 자기 지수 하나(`MARKETS[...].bench`): 코스피 KOSPI, 코스닥 KOSDAQ, 나스닥 NASDAQ(^IXIC, indices 스냅샷) — 추세추종 탭과 동일. 엔진(`sector_strength`)은 벤치마크 1~2개를 모두 지원하고 명세 원안은 QQQ·IWM이었다. ADR20 보정 RS = RS / ADR20. 섹터 값은 종목 **중앙값**. 양봉 = 종가 > 시가, RVOL20 분모는 당일 제외, 10EMA 유지는 각 거래일의 EMA와 비교 — 명세 11장 필수 테스트가 `tests/test_sector_strength.py`에 있다
- 섹터 100점은 구성 5종목 이상 + 데이터 80% 이상일 때만 산출. 결측은 0점이 아니라 UNVERIFIED. A/B는 점수 + 게이트 모두 통과
- 기준일 선택 시 그날 이하 일봉만 쓴다 (point-in-time)
- **국면별 화면** (`strategy/next_leaders.py`): 시장 지수(코스피 KOSPI, 코스닥 KOSDAQ, 나스닥 NASDAQ)의 `get_phase_label`로 국면을 나눈다. 배점은 `next_leaders.WEIGHTS` 한 곳에서 관리하고 화면 배점표도 이걸 읽는다 — 조정: 하락국면 RS/ADR 35·52주 고점 근접 35·저점 선행 20·장기 추세 10 / 찐반등 이후: 상승국면 RS/ADR 35·하락국면 20·52주 고점 근접 25·양봉 렐볼 10·장기 추세 10. 장기 추세 = 200일선 이격(위 만점, 아래 0~−15% 절반, 그 밑 0 — `trend_points`). 구간: 하락국면 = 기산점~찐반등일, 상승국면 = 찐반등 **전날** 종가~기준일(찐반등 당일 포함). 화면 배치는 국면과 무관하게 고정(시장 현황 → 차기 주도섹터 → 차기 주도주 → 🔄 순환매 → 자세히 보기, 모두 접기 가능). 국면은 순환매 섹션의 기본 펼침 여부만 바꾼다(상승 추세일 때만 펼침). 차기 주도섹터·주도주는 조정~찐반등 후 약 2주용이고, 상승 추세에서도 다음 21EMA 이탈 전까지 마지막 기산점·찐반등을 유지한다
- 순환매(`next_leaders.rotation_scores`/`rotation_sectors`): 차기 주도와 같은 재료를 최근 1~2주 롤링 구간으로. 종목 100점 = 52주 고점 근접 30 + 10EMA 5일 유지 25 + 1주 RS/ADR 15 + 양봉 렐볼 20 + 장기 추세 10 (ROT_WEIGHTS, 덜 빠짐 없음). 섹터 점수는 차기 주도섹터와 동일 방식. 🆕 = 2주 전(같은 방식 재계산) 50 미만 → 지금 60 이상 & +15. A/B 판정표는 '자세히 보기'로 이동 섹터 순위는 3종목 이상만
- '⚙️ RS 구간 설정'(탭별 키 `{market}_rs_peak`/`{market}_rs_jjin`)으로 기산점·찐반등일을 수동 지정 → `detect_phase(peak, jjin)`이 자동값을 대체. 찐반등일 지정 시 찐반등 국면, 상승 추세에서 기산점만 지정 시 '버틴 힘' 화면. 휴장일은 직전 거래일로 맞춤
- 변화 감지(`attach_trend`): 1주·2주 전 같은 구성으로 재계산해 10EMA 위 비율 추이·전주 대비 점수를 붙이고, 10EMA 회복률·RS/ADR·RVOL 확산이 함께 좋아지면 '동시 개선'

### 테마 분류 (`data/classification.py` + `scripts/classify_themes.py`)

종목당 주력 테마 1개 + 보조 태그 + 신뢰도 + 근거 URL. 고정 테마 목록 `data/taxonomy/{us,kr}.json`(버전 관리) 안에서만 고르고 LLM이 새 테마를 만들지 못한다.

- 근거 URL이 없거나 신뢰도 low면 `UNCLASSIFIED`로 두고 `suggestedTheme`만 남긴다. 미분류 종목은 섹터 집계에서 빠진다
- `overrideByUser=True`(주도섹터 탭 > 분류 검토에서 확정)는 자동 분류가 덮어쓰지 않는다. 변경 이력은 `history`
- US 배치(`run_batch_local.sh`)가 매 회차 `--scope missing`으로 신규 편입 종목만 분류한다. 테마 목록 version이 `-draft`로 끝나면 배치는 그 시장을 분류하지 않는다
- 와치리스트 '테마'·'섹터' 컬럼은 승인된 새 분류를 먼저 쓰고, 없으면 구 `theme_cache.json`으로 폴백 (`data/sector.py`)

### TradingView 내보내기 (`strategy/tv_export.py` + `ui/tv_export.py`)

`와치리스트` 제목 오른쪽의 `📤 TradingView 내보내기` 버튼 하나로 각 탭의 후보를 TradingView 워치리스트 import용 txt로 내보낸다. 포맷은 **한 줄 = 한 섹션**, 줄 맨 앞 `###제목` 뒤에 심볼을 콤마로 잇는다 (KR은 `KRX:` 접두, US는 무접두).

- 담기는 것: 리더보드 US·KR(랭킹 순) → 추세추종 3탭의 **매수 후보**(화면 필터 통과분) → 10EMA 3탭의 **셋업 완성** 종목. 순서는 `SECTION_ORDER`가 고정
- 각 탭은 렌더 첫머리에서 `register_*(market, [])`로 자기 섹션을 **먼저 비운다** — 조기 return 경로에서 직전 실행의 후보가 세션에 남아 파일에 실리는 것을 막는 장치라 제거하지 말 것
- 버튼은 탭보다 위에 있으므로 `app.py`가 제목 옆 컬럼에 `st.empty()` 슬롯만 잡아두고, **모든 탭 뒤**에서 `render_export_button(slot)`으로 채운다. 이 호출 순서가 뒤집히면 버튼이 빈 목록으로 그려진다
- 담긴 섹션·종목 수는 버튼 툴팁(`summarize_sections`)으로 보여준다 — 제목 옆 좁은 자리라 목록을 펼치면 화면을 밀어낸다
- 후보 필터는 `@st.fragment` 안이라 필터만 바꾸면 버튼이 한 박자 늦는다 — 옆의 `🔄` 버튼이 전체 재실행을 걸어 맞춘다

### 배치 데이터 신선도

- "배치가 돌았나"(회차 시각) + "종목 마지막 거래일이 같은 배치가 받은 시장 지수(KOSPI·KOSDAQ·NASDAQ)에 뒤지지 않나"를 함께 본다. 휴장일엔 지수도 그날이 없어 오경보가 없다
- `scripts/fetch_snapshot.py`는 지수를 먼저 받고, 종목이 지수보다 늦으면 5분·15분 뒤 재수집. 그래도 늦으면 `scripts/check_snapshot_fresh.py`가 fresh=false → GitHub 폴백이 다시 받는다. 앱은 `store.get_freshness`의 `behind_index`로 재수집 안내
- 2026-11-10 US 배치: NASDAQ 지수는 11/09, 종목 일괄 다운로드는 11/06까지만 받아 하루 낡은 채로 남았던 사례가 계기
- Cloud 앱의 배치 반영(`data/repo_sync.py`)은 `origin`이 아니라 공개 HTTPS 주소에서 fetch (Cloud의 origin은 인증 오류)

### 캐싱

- ⚠️ `strategy/next_leaders.py`(점수 공식·필드)를 바꾸면 `ui/sector_leadership.SCORE_VERSION`을 반드시 올릴 것 — st.cache_data는 다른 모듈 코드 변경을 감지하지 못해 배포 후 옛 캐시를 읽다 KeyError가 난다(2026-11-10 gap200 사고)
- 추세추종 탭의 '주도주 점수·순위'는 `sector_leadership.lead_scores`(주도섹터 탭과 같은 `_compute`)를 읽는다 — 점수 계산을 두 곳에 만들지 말 것
- `st.cache_data(ttl=1800)` — 지수·와치리스트 행 빌드(`ui/watchlist.py`). 캐시 키로 쓰기 위해 날짜 등 인자를 문자열로 전달하는 패턴을 유지할 것
- 파일 기반: `data/classification/*.json`(검증 테마 분류, 우선), `theme_cache.json`(구 테마 캐시 — 폴백용, `scripts/refresh_sectors.sh`), `data/themes.json`(구 테마 정의), `data/kr_names.json`(KR 종목명 번들)
- `strategy/` 모듈은 Streamlit을 import하지 않는다(단위 테스트 가능성 유지). 캐시가 필요하면 `ui/`·`data/` 계층에서 래핑

### 알아둘 것

- `app.py` 상단의 세션 최초 1회 `st.rerun()`은 AgGrid 첫 탭 레이아웃 버그 우회 — 제거 금지
- 스펙·플랜 문서: `docs/superpowers/specs/`, `docs/superpowers/plans/`
- 전담 에이전트 정의: `.claude/agents/stock-dashboard.md` — 빠른 요약만 담고 있으며, 아키텍처·컨벤션은 이 문서(CLAUDE.md)가 기준
