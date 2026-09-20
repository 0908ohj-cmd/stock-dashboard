#!/usr/bin/env python3
"""로컬 배치가 이미 스냅샷을 채웠는지 판정 — GitHub Action 폴백 게이트.

맥 launchd 배치가 1순위다. 이 스크립트는 Action이 `pip install` **전에** 돌려
"로컬이 이미 했으면 빠지고, 안 했으면 수집한다"를 결정한다. 그래서 표준
라이브러리만 쓴다 — 의존성이 깔리기 전에 실행되기 때문이다.

판정 기준은 `fetched_at`의 경과 시간이다. Action의 cron은 로컬 배치보다
늦게 잡혀 있어(KR 로컬 16:40 → Action 20:00, US 로컬 07:00 → Action 11:00),
로컬이 정상이면 갭이 3~4시간이고 실패했으면 24시간을 넘는다.

휴장일에도 `fetch_snapshot.py`는 돌아 `fetched_at`을 갱신하므로, 데이터가
안 변한 날도 "로컬이 살아 있다"로 올바르게 판정된다.

사용법: python3 scripts/check_snapshot_fresh.py --markets kr|us|all
출력:   fresh=true|false  (GITHUB_OUTPUT이 있으면 거기에도 append)
종료 코드는 항상 0 — 판정 결과는 출력으로만 전달한다.
"""
import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DEFAULT_MAX_AGE_HOURS = 8

OHLCV_DIR = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'ohlcv'

MARKET_FILES = {
    'kr':  ['KR_KOSPI.json', 'KR_KOSDAQ.json'],
    'us':  ['US.json'],
    'all': ['KR_KOSPI.json', 'KR_KOSDAQ.json', 'US.json'],
}


def snapshot_age_hours(path: pathlib.Path, now: datetime):
    """스냅샷 fetched_at의 경과 시간(시간 단위). 읽을 수 없으면 None."""
    try:
        snap = json.loads(path.read_text(encoding='utf-8'))
        fetched_at = datetime.fromisoformat(snap['fetched_at'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return (now - fetched_at).total_seconds() / 3600


def is_fresh(markets: str, now: datetime | None = None,
             max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
             ohlcv_dir: pathlib.Path | None = None) -> bool:
    """해당 시장의 스냅샷이 모두 max_age_hours 이내면 True.

    하나라도 읽을 수 없거나 오래됐으면 False — 폴백이 도는 쪽으로 기운다.
    판정 불능을 '신선'으로 보면 로컬이 죽었을 때 아무도 수집하지 않는다.
    """
    now = now or datetime.now(KST)
    directory = ohlcv_dir or OHLCV_DIR
    for name in MARKET_FILES[markets]:
        age = snapshot_age_hours(directory / name, now)
        if age is None or age > max_age_hours:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--markets', choices=['kr', 'us', 'all'], default='all')
    ap.add_argument('--max-age-hours', type=float, default=DEFAULT_MAX_AGE_HOURS)
    args = ap.parse_args()

    fresh = is_fresh(args.markets, max_age_hours=args.max_age_hours)
    line = f'fresh={"true" if fresh else "false"}'
    print(f'[{args.markets}] {line} (기준 {args.max_age_hours}시간)')

    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
