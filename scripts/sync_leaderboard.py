#!/usr/bin/env python3
"""리더보드 소스 JSON을 공통 스키마로 정규화해 대시보드 repo에 푸시한다.

이 repo에서 소스 경로를 아는 유일한 파일. 앱 코드는 data/leaderboard/*.json만 안다.
Streamlit 무의존.
"""
from datetime import datetime

# 공통 아이템 키 — US·KR 정규화 결과가 이 구성을 완전히 동일하게 갖는다
COMMON_KEYS = [
    'rank', 'ticker', 'name', 'market', 'sources', 'added_at', 'close',
    'rs_rating', 'adr', 'perf_1m', 'perf_3m', 'perf_6m', 'perf_12m',
    'dist_from_52w_high', 'avg_dollar_vol', 'theme', 'sector',
]


def _common(it: dict, *, name: str, market: str, added_at, avg_dollar_vol, sector) -> dict:
    """시장별 차이를 인자로 받고 나머지 공통 필드를 채운다."""
    return {
        'rank': None,                     # sort_and_rank가 부여
        'ticker': it.get('ticker'),
        'name': name,
        'market': market,
        'sources': it.get('sources') or [],
        'added_at': added_at,
        'close': it.get('close'),
        'rs_rating': it.get('rs_rating'),
        'adr': it.get('adr'),
        'perf_1m': it.get('perf_1m'),
        'perf_3m': it.get('perf_3m'),
        'perf_6m': it.get('perf_6m'),
        'perf_12m': it.get('perf_12m'),
        'dist_from_52w_high': it.get('dist_from_52w_high'),
        'avg_dollar_vol': avg_dollar_vol,
        'theme': it.get('theme') or '',
        'sector': sector,
    }


def normalize_us(payload: dict) -> list:
    """US 소스 → 공통 스키마. 주간 거래대금을 일평균으로 환산한다."""
    out = []
    for it in payload.get('items', []):
        dvw = it.get('dollar_vol_w')
        out.append(_common(
            it,
            name='',                                    # 소스에 종목명 없음
            market='US',
            added_at=it.get('added_at'),
            avg_dollar_vol=round(dvw / 5) if dvw else None,
            sector=None,
        ))
    return out


def normalize_kr(payload: dict) -> list:
    """KR 소스 → 공통 스키마. 거래대금은 이미 일평균이라 그대로 쓴다."""
    out = []
    for it in payload.get('items', []):
        out.append(_common(
            it,
            name=it.get('name') or '',
            market=it.get('market') or 'KR',
            added_at=None,                              # 소스에 편입일 없음
            avg_dollar_vol=it.get('avg_dollar_vol'),
            sector=it.get('sector'),
        ))
    return out


def sort_and_rank(items: list) -> list:
    """leaders 우선 → RS 내림차순 → 거래대금 내림차순. rank 1..N 부여.

    양 시장 동일 규칙이라 소스에 rank가 있어도 무시하고 재계산한다.
    """
    ordered = sorted(items, key=lambda it: (
        0 if 'leaders' in (it.get('sources') or []) else 1,
        -(it['rs_rating'] if it.get('rs_rating') is not None else -1),
        -(it.get('avg_dollar_vol') or 0),
    ))
    for i, it in enumerate(ordered, start=1):
        it['rank'] = i
    return ordered


def build_envelope(market: str, items: list, source_updated_at) -> dict:
    return {
        'market': market.upper(),
        'synced_at': datetime.now().isoformat(timespec='seconds'),
        'source_updated_at': source_updated_at,
        'count': len(items),
        'items': items,
    }
