# 리더보드 설계

- 날짜: 2026-07-29
- 상태: 설계 확정 (구현 전)

## 1. 배경 / 목적

현재 대시보드는 **사용자가 업로드한 티커**만 다룬다. TradingView 스크리너로 걸러온 종목을 추세추종·10EMA 전략으로 분석하는 구조라, "지금 시장 전체에서 누가 주도주인가"라는 질문에는 답하지 못한다.

리더보드는 그 빈자리를 메운다. 매일 시장 전체를 스캔해 IBD 방식 RS Rating 상위 종목을 뽑아둔 외부 파이프라인이 이미 존재하므로, 그 결과를 대시보드로 **미러링**해서 보여준다. 두 가지를 얻는다:

1. 시장 주도주 명단 열람 (별도 탭)
2. **내가 보는 종목이 주도주인가** — 기존 와치리스트 행에 👑 배지로 교차 표시

## 2. 경계 원칙 (중요)

**대시보드 앱은 리더보드가 어디서 왔는지 모른다.** 앱이 아는 것은 `data/leaderboard/{us,kr}.json` 파일뿐이다.

- UI 문구·에러 메시지·컬럼명 어디에도 외부 파이프라인 이름을 노출하지 않는다. 전부 "리더보드"로 부른다
- 소스 경로를 아는 유일한 코드는 `scripts/sync_leaderboard.py` 하나다
- 이 경계 덕분에 나중에 리더보드 생성 방식이 바뀌어도 앱 코드는 그대로다

## 3. 목표 / 비목표

### 목표

- US·KR 리더보드를 대시보드에서 열람 (신규 `👑 리더보드` 탭)
- 기존 와치리스트·10EMA 탭 행에 리더보드 포함 여부를 👑 배지로 표시
- 데이터 신선도를 화면에 항상 표시하고, 갱신이 끊기면 경고
- 앱은 파일만 읽는다 — 네트워크 수집 0, 로딩 없음

### 비목표

- **리더보드 종목을 이 앱의 전략(추세추종 DAY·10EMA 기준봉)으로 재분석하지 않는다.** OHLCV 수집이 필요해 로딩이 생기고, 리더보드의 목적(주도주 명단 확인)과도 어긋난다
- **리더보드 산출 로직을 이 repo로 포팅하지 않는다.** 결과만 받는다
- 리더보드 종목의 차트·상세 패널 — 티커를 TradingView 링크로 여는 것으로 충분
- 리더보드 히스토리 보관 — 최신 스냅샷만 유지

## 4. 아키텍처

```
[Mac: 리더보드 생성 파이프라인]              [GitHub: stock-dashboard]      [Streamlit Cloud]
 일간 배치  US 07:00 / KR 16:30 KST
   └ 리더보드 결과 JSON 생성
       └ scripts/sync_leaderboard.py
           ├ 소스 읽기 + 공통 스키마로 정규화
           └ GitHub Contents API ──────▶ data/leaderboard/us.json ──재배포──▶ 👑 리더보드 탭
                                          data/leaderboard/kr.json            기존 탭 👑 배지
```

동기화 스크립트를 **이 repo에 두는** 이유: 커밋 대상 repo·정규화 스키마·토큰이 전부 대시보드의 관심사다. 스키마가 바뀌어도 한 곳만 고치면 된다. 소스 쪽은 "배치가 끝나면 이 스크립트를 호출한다"만 알면 된다.

## 5. 저장 포맷

```
data/leaderboard/
├── us.json
└── kr.json
```

두 소스는 필드 구성이 서로 다르다(US는 `sources`/`added_at`/주간 거래대금, KR은 `rank`/`name`/`market`/일평균 거래대금). 앱에서 매번 분기하지 않도록 **sync 시점에 공통 스키마로 정규화**한다.

```json
{
  "market": "US",
  "synced_at": "2026-07-29T07:05:00+09:00",
  "source_updated_at": "2026-07-29T07:02:16",
  "count": 20,
  "items": [
    {
      "rank": 1,
      "ticker": "DELL",
      "name": "",
      "market": "US",
      "sources": ["leaders", "leader_ride"],
      "added_at": "2026-07-03",
      "close": 426.91,
      "rs_rating": 96,
      "adr": 7.4,
      "perf_1m": 4.4,
      "perf_3m": 97.9,
      "perf_6m": 271.6,
      "perf_12m": 236.9,
      "dist_from_52w_high": -8.2,
      "avg_dollar_vol": 3775808386,
      "theme": "AI 서버 인프라",
      "sector": null
    }
  ]
}
```

정규화 규칙:

| 공통 필드 | US 소스 | KR 소스 |
|---|---|---|
| `ticker` | `ticker` (심볼) | `ticker` (6자리 코드) |
| `name` | 소스에 없음 → `""` | `name` (한글명) |
| `market` | `"US"` 고정 | `market` (`KOSPI`/`KOSDAQ`) |
| `avg_dollar_vol` | `dollar_vol_w / 5` (주간→일평균 환산) | `avg_dollar_vol` 그대로 |
| `added_at` | `added_at` | 소스에 없음 → `null` |
| `sector` | 소스에 없음 → `null` | `sector` |
| `rank` | 아래 정렬 규칙으로 부여 | 아래 정렬 규칙으로 **재부여** |
| 나머지 | 동일 필드 그대로 | 동일 필드 그대로 |

- **정렬·rank 부여는 양 시장 동일 규칙**: 정렬 키 `(0 if "leaders" in sources else 1, -rs_rating, -avg_dollar_vol)` 오름차순 → 위에서부터 `rank` 1, 2, 3… 부여. 즉 `leaders`에 걸린 종목이 먼저 오고, 그 안에서 RS 높은 순, 동률이면 거래대금 큰 순. `rs_rating`·`avg_dollar_vol`이 `null`이면 각각 `-1`·`0`으로 취급해 뒤로 보낸다. KR 소스에 이미 `rank`가 있어도 무시하고 재계산해 양쪽 순위 의미를 일치시킨다
- 거래대금 통화는 시장에 따라 다르다(US=USD, KR=KRW). 값은 변환하지 않고 표시 단계에서 `market`을 보고 `$`/`원`을 붙인다
- 소스에 없는 필드는 `null`(또는 문자열은 `""`)로 채워 **키 구성은 양 시장 동일**하게 만든다 — 앱에서 `.get()` 분기가 필요 없도록

## 6. 동기화 스크립트 `scripts/sync_leaderboard.py`

Streamlit 무의존. 이 repo에서 유일하게 소스 경로를 아는 파일.

- CLI: `python3 scripts/sync_leaderboard.py --market us|kr|all [--local-only]`
- 소스 경로: `--source-dir` 인자 > 환경변수 `LEADERBOARD_SOURCE_DIR` > 기본값 순으로 결정 (하드코딩된 절대경로에만 의존하지 않는다)
- 토큰: 환경변수 `DASHBOARD_GITHUB_TOKEN`
- 동작:
  1. 소스 JSON 읽기 → 파싱
  2. 공통 스키마로 정규화 + 정렬·rank 부여
  3. `--local-only`면 로컬 `data/leaderboard/*.json`에 쓰고 종료 (개발·초기 생성용)
  4. 아니면 GitHub Contents API로 PUT (`app.py:_github_save`와 동일한 sha 조회 후 update 패턴)
- **로컬 repo 파일을 건드리지 않고 GitHub API만 쓴다** — Mac 로컬 작업 트리와 커밋이 충돌하지 않도록

### 실패 처리

| 상황 | 동작 |
|---|---|
| 소스 파일 없음 / 파싱 실패 | 푸시 스킵, exit 1 (기존 데이터 유지) |
| `items`가 0개 | **정상 푸시** — 빈 상태도 정보다 (§7 참조) |
| GitHub API 실패 | exit 1, 사유 로그 |
| 한쪽 시장만 실패 (`--market all`) | 다른 시장은 정상 푸시, exit 1 |

### 호출 훅

리더보드 생성 파이프라인의 US·KR 오케스트레이터 각 마지막 단계에서 이 스크립트를 subprocess로 호출한다. 호출은 `try/except`로 감싸 **실패해도 원 파이프라인은 성공 처리**한다(그쪽 기존 알림 스텝과 같은 방어 패턴). 갱신 직후에만 푸시되므로 오래된 데이터가 올라갈 일이 없다.

## 7. 빈 리더보드 정책

리더보드가 0개인 상태를 **전일 데이터로 덮지 않고 그대로 반영**한다.

이유: 리더보드 편입 기준(RS Rating ≥ 80 등)을 만족하는 종목이 하나도 없다는 것은 **주도주 부재 = 약세 신호**로, 그 자체가 정보다. 실제로 설계 시점(2026-07-29)에 KR 리더보드가 0개였다.

- 화면 문구: `조건을 충족한 종목이 없습니다 — 주도주 부재 신호일 수 있습니다`
- 단, **장애와 빈 결과는 구분한다**: 소스 파일이 없거나 파싱에 실패하면 푸시 자체를 건너뛰므로(§6) 기존 데이터가 유지된다. 빈 결과가 올라왔다는 것은 "정상 계산 결과가 0개"라는 뜻이다

## 8. 읽기 모듈 `data/leaderboard_store.py`

Streamlit 무의존 (캐시 래핑은 `ui/` 계층에서). 데이터 디렉토리가 `data/leaderboard/`라 모듈명은 `leaderboard_store.py`로 두어 이름 충돌을 피한다.

- `load(market) -> dict` — `us`/`kr` 정규화 JSON 로드. 파일이 없으면 `{"items": [], "source_updated_at": None, ...}` 형태의 빈 봉투 반환 (예외 없음)
- `get_tickers(market) -> set[str]` — 교차 배지용 티커 집합. 파일이 없으면 빈 집합
- `get_freshness(market) -> dict` — `{source_updated_at, synced_at, is_stale}` 반환

### stale 판정

예정 배치 시각: US 07:00 KST / KR 16:30 KST (평일 기준).

`last_due` = 지금 기준으로 **가장 최근에 지나간 평일 예정 배치 시각**이라 할 때:

```
is_stale = (now > last_due + 6시간) AND (source_updated_at < last_due)
```

- 두 조건이 **모두** 필요하다. 앞 조건이 없으면 배치가 정시에 성공한 직후에도 stale로 오판한다 (07:02 갱신 < 07:00+6h)
- 6시간 유예는 배치 지연 흡수용 — 유예 시간 안에는 판정을 보류한다
- `last_due`를 **평일** 기준으로 계산해, 주말과 월요일 오전에 금요일 배치분이 오탐되지 않게 한다
- 휴장일에도 배치가 돌아 갱신 시각을 남기므로, 거래일 비교가 아닌 **갱신 시각만으로** 판정한다 (설·추석 등 오탐 방지)
- `source_updated_at`이 없거나 파일이 아예 없으면 stale이 아니라 "데이터 없음"으로 취급 — 경고 대신 안내 문구

## 9. 화면

### 9.1 리더보드 탭

`app.py`의 기존 6개 탭 뒤에 `👑 리더보드`를 추가한다. 탭이 7개로 늘어나는 것을 피하려고 **US/KR은 탭 내부 라디오로 전환**한다.

- 상단: 신선도 표시 `📅 07-29 07:02 갱신 · N개` — stale이면 ⚠️ 경고 배지 추가
- 테이블(AgGrid — 기존 탭과 동일하게 정렬·필터 사용):

  `# / 티커·종목명 / 소스 / 테마 / 종가 / RS / ADR / 1M / 3M / 6M / 12M / 52H / 일평균거래대금`

- `티커·종목명`: KR은 `005930 | 삼성전자`, US는 종목명이 없으므로 티커만. TradingView 링크로 연결
- `소스`: 내부 스크리너 이름을 그대로 노출하지 않고 사용자 언어로 변환 — `leaders` → `리더`, `leader_ride` → `리더 10EMA`
- `일평균거래대금`: `market`에 따라 `$1.2B` / `1,234억` 포맷
- `RS` ≥ 80은 강조 스타일
- items가 비면 §7 문구 표시

### 9.2 교차 배지

`ui/watchlist.py`에서 `'티커 | 종목명'` 표시 문자열을 만드는 지점(현재 494행 부근)에서, 티커가 해당 시장 리더보드 집합에 있으면 앞에 `👑`를 붙인다.

- 코스피·코스닥 탭 → `kr` 집합, 나스닥 탭 → `us` 집합
- `ui/watchlist_10ema.py`에도 동일 적용
- 리더보드 파일이 없으면 빈 집합이 되어 배지가 그냥 안 붙는다 — 기존 동작과 완전히 동일

## 10. 모듈 구성

| 파일 | 역할 | 계층 |
|---|---|---|
| `scripts/sync_leaderboard.py` | 소스 읽기 → 정규화 → 푸시 (신규) | 배치 |
| `data/leaderboard_store.py` | 정규화 JSON 로드·티커 집합·신선도 (신규) | data |
| `ui/leaderboard.py` | 리더보드 탭 렌더 (신규) | ui |
| `app.py` | 탭 추가 (수정) | ui |
| `ui/watchlist.py` | 👑 배지 (수정) | ui |
| `ui/watchlist_10ema.py` | 👑 배지 (수정) | ui |

기존 3계층 규칙을 그대로 따른다 — `data/`·`scripts/`는 Streamlit을 import하지 않고, 캐시가 필요하면 `ui/`에서 `st.cache_data`로 래핑한다.

## 11. 테스트

`tests/test_leaderboard_store.py`

- `load`: 정상 파일 / 파일 없음 / `items` 빈 배열
- `get_tickers`: 티커 집합 반환, 파일 없으면 빈 집합
- `get_freshness`: 신선 / stale / 주말(금요일 배치가 월요일 오전에 오탐되지 않을 것) / 파일 없음

`tests/test_sync_leaderboard.py`

- US 소스 → 공통 스키마 정규화 (주간 거래대금 `/5` 환산 확인)
- KR 소스 → 공통 스키마 정규화 (`name`·`sector` 유지, `added_at` null)
- 정렬·rank 부여 규칙 (leaders 소스 우선, RS 내림차순)
- 양 시장 정규화 결과의 **키 구성이 동일**할 것
- 소스 파일 없음 → 푸시 호출 없이 exit 1 (GitHub API 모킹)
- `items` 0개 → 정상 푸시

회귀: `python3 -m pytest tests/ --ignore=tests/test_scoring.py`

## 12. 롤아웃

1. `data/leaderboard_store.py` + 테스트 (앱 동작 변화 없음)
2. `scripts/sync_leaderboard.py` + 테스트 → `--local-only`로 1회 실행해 `data/leaderboard/*.json` 초기 생성·커밋
3. `ui/leaderboard.py` + `app.py` 탭 추가 → 화면 확인
4. 교차 배지 적용 (`watchlist.py`, `watchlist_10ema.py`)
5. 생성 파이프라인에 호출 훅 추가 + `DASHBOARD_GITHUB_TOKEN` 설정 → 다음 배치에서 자동 푸시 확인

### 사용자 준비 사항

Mac의 리더보드 생성 파이프라인 환경(`.env`)에 이 repo 쓰기 권한이 있는 GitHub 토큰을 `DASHBOARD_GITHUB_TOKEN`으로 등록해야 한다. Streamlit secrets에 이미 쓰고 있는 `GITHUB_TOKEN`과 같은 토큰을 재사용할 수 있다.

## 13. 에러 처리 요약

| 상황 | 동작 |
|---|---|
| 소스 파일 없음/파싱 실패 | 푸시 스킵, 기존 데이터 유지 |
| 리더보드 0개 | 그대로 푸시, 화면에 "주도주 부재 신호" 안내 |
| 배치 미실행 (stale 판정) | 탭 상단 ⚠️ 경고 배지 |
| `data/leaderboard/*.json` 없음 | 탭은 "데이터 없음" 안내, 배지는 미표시 (기존 동작과 동일) |
| GitHub API 실패 | sync exit 1, 원 파이프라인은 성공 유지 |
