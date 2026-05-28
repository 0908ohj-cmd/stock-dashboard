# 10EMA 강세장 와치리스트 탭 설계 (2026-05-28)

## 개요

기존 21EMA 조정 차기주도주 탭(코스피/코스닥/나스닥)과 완전히 분리된
10EMA 강세장 주도주 추종 탭 2개를 신설한다.

- **10EMA 국장**: 한국 주식 (코스피+코스닥 통합 업로드)
- **10EMA 미장**: 미국 주식

---

## 탭 구조

| 탭 번호 | 탭명 | 업로드 | 전략 |
|---------|------|--------|------|
| 1 | 코스피 | kospi.tickers | 21EMA 조정 RS |
| 2 | 코스닥 | kosdaq.tickers | 21EMA 조정 RS |
| 3 | 10EMA 국장 | 10ema_kr.tickers | 10EMA 기준봉 |
| 4 | 나스닥 | us.tickers | 21EMA 조정 RS |
| 5 | 10EMA 미장 | 10ema_us.tickers | 10EMA 기준봉 |

탭 3, 5는 기존 탭과 코드 공유 없이 완전 독립 구현.

---

## 기준봉 정의

기관의 실질 매집이 일어난 봉. 아래 조건을 **모두** 충족해야 한다.

### 필수 조건
1. **거래량**: 직전 20거래일 평균 대비 **300% 이상**
2. **바디**: 종가가 당일 레인지 상위 **30% 이내** (긴 아랫꼬리 양봉 허용)
3. **저항 돌파** (둘 중 하나):
   - 직전 **60거래일** 최고가 돌파 (종가 기준)
   - **횡보 박스** 상단 돌파: 직전 5~20일 내 고가-저가 범위가 5% 이내인 구간의 상단 종가 돌파
4. **정배열**: 기준봉 당일 기준 **EMA10 > EMA21 > EMA50**

### 선택 기준
- 위 조건을 충족하는 봉이 **63거래일(약 3개월)** 이내에 여러 개이면, **거래량 비율이 가장 높은 봉** 1개를 기준봉으로 선택

---

## 케이스 분류 (현재가 기준)

### Case 1 — 기준봉 범위 내 횡보 (매수 대기)
- 현재가: 기준봉 **중간(midline) ~ 고가** 사이
- 횡보 기간: 기준봉 이후 **3~15 거래일** 경과
- 10EMA **우상향** (직전 5일 기울기 > 0)
- 현재가 10EMA 위

### Case 2 — 기준봉 위로 올라갔다 복귀 (재진입 대기)
- 기준봉 이후 **30거래일 이내**에 기준봉 고가를 돌파한 적 있음
- 현재가가 기준봉 **고가 ± 3%** 이내로 복귀
- 10EMA **우상향**
- 현재가 10EMA 위

### 대기중
- 기준봉 존재하나 Case 1 / Case 2 조건 미충족
- 아직 기준봉 범위에 있거나, 횡보 기간 초과, 10EMA 아래 등

### 하방이탈
- 현재가가 기준봉 **저가 아래**로 이탈
- 셋업 무효화 — 리스트에 표시하되 별도 표시

### 기준봉없음
- 63거래일 이내 유효 기준봉 없음

---

## 10EMA 우상향 판정

```
최근 5거래일 EMA10 기울기 = (EMA10[-1] - EMA10[-5]) / EMA10[-5] * 100
기울기 > 0 이면 우상향
```

---

## 표시 컬럼

| 컬럼명 | 설명 |
|--------|------|
| 티커\|종목명 | 종목 코드 + 이름 |
| 섹터 | 섹터 (가능한 경우) |
| Close | 현재가 |
| 등락% | 전일 대비 |
| 케이스 | Case1 / Case2 / 대기중 / 하방이탈 / 기준봉없음 |
| 기준봉일 | 기준봉 날짜 |
| 기준봉거래량비 | 기준봉 당일 거래량 / 20일 평균 (배수) |
| 횡보일수 | 기준봉 이후 현재까지 거래일 수 |
| 10EMA기울기% | 최근 5일 EMA10 기울기 |
| 고점대비% | 52주 고점 대비 현재가 |
| MA점수 | 이동평균 정배열 점수 (기존 로직 재사용) |

정렬: 기본값 — Case1 → Case2 → 대기중 → 하방이탈 → 기준봉없음, 같은 케이스 내에서는 기준봉거래량비 내림차순

---

## 파일 구조

### 신규 파일
```
strategy/pivot_candle.py       # 기준봉 탐지 + 케이스 분류
ui/watchlist_10ema.py          # 10EMA 탭 렌더링 (st-aggrid)
data/saved/10ema_kr.tickers    # 국장 10EMA 와치리스트
data/saved/10ema_us.tickers    # 미장 10EMA 와치리스트
tests/test_pivot_candle.py     # 기준봉 탐지 단위테스트
```

### 수정 파일
```
app.py                         # 탭 5개로 확장, 업로더 2개 추가
```

### 변경 없음
```
strategy/rs_correction.py
strategy/market_status.py
strategy/indicators.py
ui/watchlist.py
data/fetcher.py
```

---

## strategy/pivot_candle.py 인터페이스

```python
def find_pivot_candle(
    stock_df: pd.DataFrame,
    lookback: int = 63,
) -> dict | None:
    """
    최근 lookback 거래일 내 기준봉 탐지.
    반환: {'date': Timestamp, 'vol_ratio': float, 'high': float,
           'low': float, 'midline': float, 'close': float}
    없으면 None.
    """

def classify_case(
    stock_df: pd.DataFrame,
    pivot: dict | None,
) -> str:
    """
    '기준봉없음' | '하방이탈' | '대기중' | 'Case1' | 'Case2'
    """

def calc_10ema_slope(stock_df: pd.DataFrame, period: int = 5) -> float:
    """최근 period일 EMA10 기울기 (%)"""
```

---

## app.py 변경 범위

1. `SAVED_PATHS`에 `'10EMA_KR'`, `'10EMA_US'` 키 추가
2. `st.tabs([...])` — 5개로 확장
3. 탭 3, 5: `ui/watchlist_10ema.render_tab()` 호출
4. 탭 1, 2, 4: 기존 코드 그대로

---

## 테스트 계획

`tests/test_pivot_candle.py` 최소 케이스:

1. 고점 돌파 + 충분한 거래량 → 기준봉 탐지
2. 거래량 미달 → 기준봉 없음
3. 정배열 미충족 → 기준봉 없음
4. 복수 후보 → 거래량 가장 높은 것 선택
5. Case 1 분류 (횡보 기간 3~15일, 가격 범위 내)
6. Case 1 실패 — 횡보 기간 초과 (16일+)
7. Case 2 분류 (30일 내 돌파 후 복귀)
8. 하방이탈 분류
9. `calc_10ema_slope` — 우상향/하락 판별
