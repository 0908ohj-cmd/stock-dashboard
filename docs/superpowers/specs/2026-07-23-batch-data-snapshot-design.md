# 배치 수집 + 저장소 우선 표시 설계

- 날짜: 2026-07-23
- 상태: 설계 확정 (구현 전)

## 1. 배경 / 문제

현재 앱은 분석 결과를 어디에도 영속화하지 않는다. 캐시는 `st.cache_data`(서버 메모리 + TTL 30분)뿐이라:

- Streamlit Cloud 슬립 해제·재배포 시 캐시가 전멸 → 방문할 때마다 전 종목 시세를 네트워크로 재수집
- 지수 패널은 TTL 5분이라 사실상 매번 재수집
- 찐반등 상태에서는 `_build_rows`가 as_of 스냅샷(찐반등일·DAY3·DAY4)별로 최대 4회 호출되는데, `fetch_daily`에 캐시가 없어 같은 시세를 반복 수집

분석 대상이 전부 **일봉 종가 기준**이라 실시간성이 필요 없다. "정해진 시각에 수집 → 보존 → 표시" 모델이 맞다.

## 2. 목표 / 비목표

### 목표

- 장 마감 후 GitHub Actions 배치가 OHLCV를 수집해 repo에 커밋 → Streamlit Cloud 자동 재배포 → 앱은 번들 파일을 읽기만 함 (로딩 사실상 제거)
- 데이터 기준 시점("어느 거래일 장마감 기준, 언제 수집")을 화면에 항상 표시
- 배치 실패를 감지해 경고 (자동 복구는 하지 않음 — 수동 새로고침으로 복구)
- 신규 티커 업로드 시에는 해당 티커만 온디맨드 수집 (미스 폴백)

### 비목표

- 10EMA 탭 유니버스 스냅샷 — 실험 단계라 제외, 기존 방식(메모리 캐시 1시간) 유지
- 5분봉 차트 저장 — 온디맨드 소량 수집 유지
- 섹터 캐시 변경 없음 (`sector_cache.json` 유지)
- 증분(append) 캐시 — 하지 않음. 매 배치 전체 교체로 액면분할 등 보정 문제 원천 차단

## 3. 아키텍처 개요

```
[GitHub Actions cron]                     [Streamlit Cloud]
scripts/fetch_snapshot.py                 app.py
  ├ data/saved/*.tickers 읽기              ├ data/store.py 로더로 parquet 읽기 (네트워크 0)
  ├ fetch_daily (pykrx/FDR 패치 체인)      ├ 미스 시에만 fetch_daily 온디맨드 폴백
  ├ data/ohlcv/*.parquet 전체 교체         ├ meta.json으로 신선도 배지 표시
  └ main에 커밋·push ──────────────────▶  └ push = 자동 재배포 트리거
```

## 4. 데이터 저장소

```
data/ohlcv/
├── KR_KOSPI.parquet    # long format: Ticker, Date, Open, High, Low, Close, Volume
├── KR_KOSDAQ.parquet
├── US.parquet
├── indices.parquet     # Index(KOSPI·KOSDAQ·NASDAQ), Date, OHLCV — 400일
└── meta.json
```

- 종목 시세는 350달력일(≈240거래일) — 현재 `fetch_daily(days=350)`와 동일. SMA200·52주 고점 계산 요건
- `meta.json` 스키마:

```json
{
  "KR_KOSPI":  {"fetched_at": "2026-07-23T16:45:12+09:00", "last_trading_date": "2026-07-23", "tickers": 87, "failed": 2},
  "KR_KOSDAQ": {"...": "..."},
  "US":        {"...": "..."},
  "indices":   {"fetched_at": "...", "last_trading_date": "..."}
}
```

- 매 배치 **전체 교체** (해당 시장 parquet을 통째로 다시 씀). 소스(yfinance `auto_adjust`, pykrx)가 보정한 값으로 전량 갱신되므로 분할·배당 보정 문제 없음
- 예상 크기: 전체 수백 KB~수 MB (repo 부담 없음)
- `pyarrow`를 `requirements.txt`에 추가

## 5. GitHub Actions 배치

### `scripts/fetch_snapshot.py`

- CLI: `python scripts/fetch_snapshot.py --markets kr|us|all`
- 동작:
  1. `data/saved/{kospi,kosdaq,us}.tickers`에서 티커 로드
  2. 시장별로 `fetch_daily` 재사용 (KR 패치 체인 그대로 경유) — 순차 수집이어도 러너에서는 무방
  3. 지수 3종은 kr/us 어느 실행이든 항상 갱신 (비용 미미)
  4. parquet 전체 교체 + `meta.json` 갱신 (휴장일이어도 `fetched_at`은 항상 갱신)
- 실패 처리:
  - 개별 티커 실패 → 스킵하고 계속, `meta.json`의 `failed` 카운트에 기록
  - 시장 단위 대량 실패(성공률 70% 미만) → 해당 시장 parquet을 **교체하지 않고** 종료 코드 1 (이전 데이터 유지, Actions 잡 실패로 표시)
- Streamlit 무의존 (data/ 계층만 import)

### `.github/workflows/fetch-data.yml`

- 트리거:
  - `schedule`: KR `45 7 * * 1-5` (UTC, = 16:45 KST 월~금) → `--markets kr`
  - `schedule`: US `0 22 * * 1-5` (UTC, = 07:00 KST 화~토, EDT 18:00/EST 17:00 마감 후) → `--markets us`
  - `workflow_dispatch`: 수동 실행 (markets 입력 가능) — 비상 복구용
- 잡 순서: checkout → python 3.11 → 의존성 설치(`requirements-batch.txt` — pandas·pyarrow·yfinance·pykrx·finance-datareader만, streamlit 제외) → 스크립트 실행 → `git pull --rebase` 후 `data/ohlcv/` 커밋·push
- 커밋 메시지: `data: KR 스냅샷 2026-07-23` 형식
- push → Streamlit Cloud 자동 재배포 (하루 2회 재시작 발생, 허용)
- cron은 정시 보장이 없어 15~60분 지연 가능 — 장 마감 배치 특성상 문제 없음
- repo 60일 비활성 시 schedule 자동 비활성화 규칙은 매일 커밋으로 해당 없음

## 6. 앱 읽기 경로 — 새 모듈 `data/store.py`

Streamlit 무의존 (캐시 래핑은 ui/ 계층에서).

- `load_daily(ticker, market) -> DataFrame`
  - 해당 시장 parquet에 티커가 있으면 즉시 반환 (fetch_daily와 동일한 형태: DatetimeIndex + OHLCV 컬럼)
  - **미스 시** (신규 업로드 티커): 기존 `fetch_daily`로 수집 → 컨테이너 로컬 parquet에 병합 저장 후 반환. 다음 배치가 돌면 repo에 영구 반영
- `load_index(name) -> DataFrame` — indices.parquet에서 로드, 미스 시 `fetch_index_daily` 폴백
- `get_freshness(market) -> dict` — meta.json 기반 `{fetched_at, last_trading_date, is_stale}` 반환
- `refetch_market(market)` — 해당 시장 전체를 fetch_daily로 재수집해 로컬 parquet 교체 (수동 새로고침용)

### 호출부 교체

- `ui/watchlist.py`: `_build_rows` 내 `fetch_daily` → `store.load_daily`, `_fetch_index_cached` → `store.load_index` 래핑. `st.cache_data`는 유지 (미스여도 로컬 읽기라 수 초)
- `ui/index_panel.py`: `_load_index` → `store.load_index` 래핑
- as_of 스냅샷(찐반등일·DAY3·DAY4)의 `_build_rows` 반복 호출은 자연히 같은 로컬 데이터를 공유 → 네트워크 중복 소멸
- `ui/watchlist_10ema.py`·5분봉(`fetch_intraday_*`)·섹터는 변경 없음

### 업로드 흐름 (기존 유지 + 폴백)

1. CSV/TXT 업로드 → `data/saved/*.tickers` 저장 + `_github_save` 커밋 (기존 그대로)
2. 분석 시 신규 티커는 store 미스 폴백으로 즉시 수집 → 업로드 직후 1회 분석이 자동 수행됨
3. 알려진 특성: `_github_save` 커밋이 재배포를 유발하면 컨테이너 로컬 수집분은 소실되나, 재배포 후 미스 폴백이 다시 채운다. 다음 배치부터는 repo 번들에 포함

## 7. 신선도 표시 + 실패 감지

- 각 추세추종 탭 상단 + 지수 패널에 항상 표시:
  - `📅 07-23 장마감 기준 · 수집 07-23 16:45`
- **실패 판정 = meta.json `fetched_at`이 "직전 평일 배치 예정 시각 + 6시간"보다 이전** (사실상 30시간 룰이되, 주말·월요일 오전에 마지막 금요일 배치가 오탐되지 않도록 직전 평일 기준으로 계산)
  - 거래일 비교 방식은 쓰지 않는다 — 설날·추석 등 휴장일 오탐 방지. 배치가 휴장일에도 `fetched_at`을 갱신하므로 수집시각만으로 "배치가 돌았는가"를 정확히 판정 가능
- 실패 감지 시: ⚠️ **"배치 수집이 실패한 것 같습니다 — 사이드바에서 수동 새로고침 하세요"** 경고 배지만 표시. **자동 재수집은 하지 않는다**
- `meta.json`이 아예 없으면(마이그레이션 직후 등) 경고 없이 미스 폴백으로 동작

## 8. 수동 새로고침

- 사이드바 기존 "🔄 새로고침" 버튼 → **"🔄 데이터 재수집"**으로 변경: `store.refetch_market` 전 시장 실행 + 캐시 클리어 + rerun (비상용, 스피너 표시)
- 각 탭 내 기존 "재스캔" 버튼 → 해당 시장만 `refetch_market` + 캐시 클리어로 변경
- "⚠️ 주가 데이터는 15분 지연" 캡션 → "장 마감 후 배치 수집 데이터" 안내로 교체

## 9. 에러 처리 요약

| 상황 | 동작 |
|------|------|
| 배치에서 개별 티커 실패 | 스킵, meta.json failed 카운트 |
| 배치 시장 단위 대량 실패 (성공률 <70%) | parquet 미교체, 잡 실패 (이전 데이터 유지) |
| 배치 자체 미실행 (신선도 판정 초과) | 앱에서 ⚠️ 경고 배지, 수동 새로고침 유도 |
| 앱에서 parquet에 티커 없음 | fetch_daily 온디맨드 폴백 + 로컬 병합 |
| parquet/meta.json 파일 없음 | 전체 미스 폴백 (기존 동작과 동일), 경고 없음 |
| 온디맨드 폴백 실패 | 해당 티커 스킵 (기존 `_build_rows`의 예외 처리 유지) |

## 10. 테스트

- `tests/test_store.py`:
  - parquet 히트 시 fetch_daily 미호출 (모킹)
  - 미스 시 폴백 호출 + 로컬 병합 저장
  - `get_freshness` 판정: 신선/초과/meta 없음/주말 케이스
- `scripts/fetch_snapshot.py`: 티커 2~3개로 스모크 테스트 (실수집), 성공률 게이트 로직은 단위 테스트 (모킹)
- 기존 테스트 회귀: `python3 -m pytest tests/ --ignore=tests/test_scoring.py`

## 11. 롤아웃

1. `data/store.py` + 테스트 (앱 동작 변화 없음)
2. `scripts/fetch_snapshot.py` + 로컬 1회 실행으로 `data/ohlcv/` 초기 생성·커밋
3. ui 호출부 교체 + 신선도 배지 + 새로고침 버튼 변경
4. `.github/workflows/fetch-data.yml` 추가, `workflow_dispatch`로 1회 검증
5. 다음 거래일 배치 자동 실행 확인
