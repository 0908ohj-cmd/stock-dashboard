"""TradingView 워치리스트 import용 txt 생성.

포맷은 TradingView가 받는 것 그대로다 — 한 줄이 한 섹션이고, 줄 맨 앞의
`###제목`이 그 줄에 이어지는 심볼들의 그룹명이 된다:

    ###🇰🇷 코스피 후보,KRX:005930,KRX:000660
    ###🇺🇸 나스닥 후보,NVDA,AVGO

Streamlit 무의존 — 파일 생성은 순수 문자열 조립이라 UI 없이 테스트한다.
"""
from datetime import datetime
from typing import Iterable

# 한국 거래소 심볼 접두사. TradingView는 코스피·코스닥을 모두 KRX로 묶는다.
_KRX_PREFIX = 'KRX:'

# 파일 안 섹션 순서. 탭은 화면 순서와 무관하게 등록되므로 여기서 한 번 고정해
# 매번 같은 자리에서 같은 그룹이 나오게 한다. 주도주(리더보드)를 맨 위에 둔다.
SECTION_ORDER = (
    'leaderboard_us',
    'leaderboard_kr',
    'trend_KR_KOSPI',
    'trend_KR_KOSDAQ',
    'trend_US',
    'ema10_KR_KOSPI',
    'ema10_KR_KOSDAQ',
    'ema10_US',
)


def to_tv_symbol(ticker, market: str) -> str | None:
    """티커 하나를 TradingView 심볼로. 쓸 수 없는 값이면 None.

    시세 수집이 실패한 행은 티커 자리에 None/NaN을 남길 수 있다 — 한 종목 때문에
    export 전체가 죽지 않도록 여기서 조용히 걸러낸다.
    """
    if not isinstance(ticker, str):
        return None
    symbol = ticker.strip()
    if not symbol:
        return None
    # 이미 거래소 접두사가 붙어 있으면(사용자가 올린 원본 표기 등) 그대로 둔다
    if ':' in symbol:
        return symbol
    # 시장 코드는 호출부마다 표기가 다르다 — 와치리스트 탭은 'KR_KOSPI',
    # 리더보드 저장소는 'kr'. 대소문자로 접두사가 갈리지 않게 맞춰서 본다.
    if market.upper().startswith('KR'):
        return _KRX_PREFIX + symbol
    return symbol


def _symbols(tickers: Iterable, market: str) -> list:
    """쓸 수 있는 심볼만 입력 순서대로. 중복은 첫 자리를 남기고 지운다.

    파일에 나가는 목록과 화면에 세어 보이는 개수가 갈리지 않도록 양쪽이 이 함수를 쓴다.
    """
    symbols = []
    for t in tickers:
        symbol = to_tv_symbol(t, market)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def build_section(title: str, tickers: Iterable, market: str) -> str | None:
    """한 섹션을 `###제목,SYM1,SYM2` 한 줄로. 담을 심볼이 없으면 None.

    빈 섹션에서 None을 돌려주는 게 핵심이다 — `###제목`만 남은 줄을 내보내면
    TradingView 쪽에 빈 그룹이 생긴다.
    """
    symbols = _symbols(tickers, market)
    if not symbols:
        return None
    return ','.join([f'###{title}', *symbols])


def summarize_sections(sections: Iterable[tuple[str, Iterable, str]]) -> str:
    """`제목 N개` 줄 묶음 — 버튼을 누르기 전에 무엇이 담겼는지 보여주는 용도."""
    lines = []
    for title, tickers, market in sections:
        count = len(_symbols(tickers, market))
        if count:
            lines.append(f'{title} {count}개')
    return '\n'.join(lines)


def build_export(sections: Iterable[tuple[str, Iterable, str]]) -> str:
    """`(제목, 티커들, 시장)` 목록을 파일 본문 하나로. 쓸 섹션이 없으면 빈 문자열."""
    lines = []
    for title, tickers, market in sections:
        line = build_section(title, tickers, market)
        if line:
            lines.append(line)
    if not lines:
        return ''
    return '\n'.join(lines) + '\n'


def order_sections(registry: dict) -> list:
    """등록된 섹션 dict를 `SECTION_ORDER` 순서의 리스트로.

    `SECTION_ORDER`에 없는 키는 버리지 않고 뒤에 붙인다 — 탭이 늘었는데 순서
    상수 갱신을 잊었을 때 종목이 조용히 사라지는 쪽이 순서가 어긋나는 것보다 나쁘다.
    """
    ordered = [registry[key] for key in SECTION_ORDER if key in registry]
    ordered += [registry[key] for key in registry if key not in SECTION_ORDER]
    return ordered


def export_filename(now: datetime) -> str:
    """받는 쪽에서 언제 뽑은 목록인지 알 수 있게 분 단위 스탬프를 붙인다."""
    return f'watchlist_tv_{now:%Y%m%d_%H%M}.txt'
