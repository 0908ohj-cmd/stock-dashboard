#!/usr/bin/env python3
"""리더보드 소스 JSON을 공통 스키마로 정규화해 대시보드 repo에 푸시한다.

이 repo에서 소스 경로를 아는 유일한 파일. 앱 코드는 data/leaderboard/*.json만 안다.
Streamlit 무의존.
"""
import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime

import requests

GITHUB_REPO = '0908ohj-cmd/stock-dashboard'
OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'leaderboard'

# 소스 경로 — 이 repo에서 외부 파이프라인 위치를 아는 유일한 지점
DEFAULT_SOURCE_DIR = pathlib.Path.home() / 'Workspace' / 'stockEdge' / 'data'
_SOURCE_FILENAME = {'us': 'leaderboard.json', 'kr': 'leaderboard_kr.json'}

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


def load_source(market: str, source_dir) -> dict:
    """소스 JSON 로드. 없으면 FileNotFoundError — 호출부가 푸시를 건너뛴다."""
    path = pathlib.Path(source_dir) / _SOURCE_FILENAME[market]
    if not path.exists():
        raise FileNotFoundError(f'리더보드 소스를 찾을 수 없습니다: {path}')
    return json.loads(path.read_text(encoding='utf-8'))


def get_token() -> str:
    """DASHBOARD_GITHUB_TOKEN → gh auth token 순으로 조회."""
    token = os.environ.get('DASHBOARD_GITHUB_TOKEN', '').strip()
    if token:
        return token
    try:
        out = subprocess.run(['gh', 'auth', 'token'], capture_output=True,
                             text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    raise RuntimeError(
        'GitHub 토큰을 찾을 수 없습니다. '
        'DASHBOARD_GITHUB_TOKEN 환경변수를 설정하거나 gh CLI로 로그인하세요.')


def _comparable(envelope: dict) -> dict:
    """내용 비교용 사본 — synced_at은 매 실행마다 달라지므로 제외한다."""
    return {k: v for k, v in envelope.items() if k != 'synced_at'}


def _decode_remote(payload: dict) -> dict | None:
    """Contents API 응답에서 현재 원격 봉투를 복원. 실패하면 None(=비교 불가)."""
    try:
        raw = base64.b64decode(payload.get('content') or '')
        data = json.loads(raw.decode('utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def push_to_github(market: str, envelope: dict, token: str) -> bool:
    """Contents API로 data/leaderboard/{market}.json 갱신 (sha 조회 후 update).

    내용이 원격과 같으면(synced_at 제외) PUT을 건너뛴다 — 그러지 않으면 매 실행마다
    빈 커밋이 쌓이고 그때마다 Streamlit Cloud가 재배포된다.
    푸시했으면 True, 건너뛰었으면 False.
    """
    path = f'data/leaderboard/{market}.json'
    url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{path}'
    hdrs = {'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json'}
    r = requests.get(url, headers=hdrs, timeout=10)
    sha, remote = None, None
    if r.ok:
        payload = r.json()
        if isinstance(payload, dict):
            sha = payload.get('sha')
            remote = _decode_remote(payload)      # sha 조회 응답을 그대로 재사용

    if remote is not None and _comparable(remote) == _comparable(envelope):
        print(f'[{market}] 원격과 동일 — 푸시 건너뜀')
        return False

    content = json.dumps(envelope, ensure_ascii=False, indent=2)
    body = {'message': f'data: 리더보드 {market.upper()} {envelope["count"]}종목',
            'content': base64.b64encode(content.encode()).decode()}
    if sha:
        body['sha'] = sha
    resp = requests.put(url, json=body, headers=hdrs, timeout=20)
    if not resp.ok:
        raise RuntimeError(f'GitHub 푸시 실패 ({resp.status_code}): {resp.text[:200]}')
    return True


def sync_market(market: str, source_dir, local_only: bool, token) -> dict:
    """한 시장을 정규화해 로컬 저장 또는 GitHub 푸시."""
    payload = load_source(market, source_dir)
    normalize = normalize_us if market == 'us' else normalize_kr
    items = sort_and_rank(normalize(payload))
    envelope = build_envelope(market, items, payload.get('updated_at'))

    if local_only:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / f'{market}.json').write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        push_to_github(market, envelope, token)
    return envelope


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='리더보드 동기화')
    ap.add_argument('--market', choices=['us', 'kr', 'all'], default='all')
    ap.add_argument('--source-dir', default=None)
    ap.add_argument('--local-only', action='store_true',
                    help='GitHub에 푸시하지 않고 로컬 파일만 갱신 (개발·초기 생성용)')
    args = ap.parse_args(argv)

    source_dir = pathlib.Path(
        args.source_dir
        or os.environ.get('LEADERBOARD_SOURCE_DIR')
        or DEFAULT_SOURCE_DIR)

    markets = ['us', 'kr'] if args.market == 'all' else [args.market]
    token = None if args.local_only else get_token()

    failed = []
    for market in markets:
        try:
            env = sync_market(market, source_dir, args.local_only, token)
            print(f'[{market}] {env["count"]}종목 · 소스 갱신 {env["source_updated_at"]}')
        except Exception as e:
            print(f'[{market}] 실패: {e}', file=sys.stderr)
            failed.append(market)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
