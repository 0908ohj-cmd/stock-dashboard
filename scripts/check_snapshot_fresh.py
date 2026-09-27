#!/usr/bin/env python3
"""로컬 배치가 이번 회차를 이미 수집했는지 판정 — GitHub Action 폴백 게이트.

맥 launchd 배치가 1순위다. Action 폴백은 이 게이트로 "로컬이 이번 회차를
돌았으면 빠지고, 안 돌았으면 대신 수집한다"를 정한다.

판정 기준은 '회차'다: 스냅샷 fetched_at이 가장 최근 로컬 회차 시각 이후면 신선.
  KR 로컬 회차: 월~금 16:40 KST     US 로컬 회차: 화~토 09:00 KST
"수집 후 N시간 이내"로 판정하면 안 된다 — GitHub cron은 실측 3~6.5시간 늦게
뜬다(2026-09: KR 06:45Z 예약 → 12:01~13:19Z, US 00:00Z → 03:12~03:35Z).
20:00 KST로 예약한 KR 폴백이 새벽 2시 반에 뜨면 로컬 수집(16:44)에서 이미
10시간이 지나, 로컬이 멀쩡한데도 폴백이 매일 다시 수집하게 된다. 회차 기준은
지연이 하루를 넘지 않는 한 영향받지 않는다.

휴장일에도 로컬 배치는 돌아 fetched_at을 갱신하므로(fetch_snapshot.py),
데이터가 안 변한 날도 "로컬이 이번 회차를 돌았다"로 올바르게 판정된다.

표준 라이브러리만 쓴다 — Action이 pip install 전에 돌리기 때문이다.

사용법: python3 scripts/check_snapshot_fresh.py --markets kr|us|all [--ref REF]
  --ref  작업본 대신 git ref(예: origin/main)의 스냅샷을 판정한다. Action이 자기
         수집을 끝낸 뒤 "그 사이 로컬이 올렸나"를 볼 때 쓴다.
출력:   fresh=true|false  (GITHUB_OUTPUT이 있으면 거기에도 append)
종료 코드는 항상 0 — 판정 결과는 출력으로만 전달한다.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
OHLCV_DIR = REPO_ROOT / 'data' / 'ohlcv'

# 로컬 launchd 회차 — scripts/launchd/*.plist와 반드시 같아야 한다
LOCAL_SLOTS = {
    'kr': {'hour': 16, 'minute': 40, 'weekdays': {0, 1, 2, 3, 4}},   # 월~금
    'us': {'hour': 9,  'minute': 0,  'weekdays': {1, 2, 3, 4, 5}},   # 화~토
}
MARKET_FILES = {
    'kr': ['KR_KOSPI.json', 'KR_KOSDAQ.json'],
    'us': ['US.json'],
}
# 맥과 러너 시계가 몇 분 어긋나는 정도는 허용한다. 그 이상 미래면 시계 오류로 본다.
FUTURE_TOLERANCE = timedelta(minutes=10)


def last_local_slot(market: str, now: datetime) -> datetime:
    """now 이전(포함) 가장 최근 로컬 배치 회차 시각."""
    slot = LOCAL_SLOTS[market]
    d = now.astimezone(KST).date()
    for _ in range(8):
        cand = datetime(d.year, d.month, d.day, slot['hour'], slot['minute'], tzinfo=KST)
        if cand.weekday() in slot['weekdays'] and cand <= now:
            return cand
        d -= timedelta(days=1)
    raise AssertionError('회차를 찾지 못했다')   # 주 5회 회차라 8일 안에 반드시 있다


def _read(name: str, ohlcv_dir: pathlib.Path, ref: str | None) -> str | None:
    if ref:
        rel = (ohlcv_dir / name).resolve().relative_to(_git_root(ohlcv_dir))
        r = subprocess.run(['git', 'show', f'{ref}:{rel.as_posix()}'],
                           cwd=ohlcv_dir, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None
    try:
        return (ohlcv_dir / name).read_text(encoding='utf-8')
    except OSError:
        return None


def _git_root(path: pathlib.Path) -> pathlib.Path:
    r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                       cwd=path, capture_output=True, text=True, check=True)
    return pathlib.Path(r.stdout.strip()).resolve()


def _fetched_at(text: str | None) -> datetime | None:
    if text is None:
        return None
    try:
        return datetime.fromisoformat(json.loads(text)['fetched_at'])
    except (ValueError, KeyError, TypeError):
        return None


def is_fresh(markets: str, now: datetime | None = None,
             ohlcv_dir: pathlib.Path | None = None, ref: str | None = None) -> bool:
    """해당 시장의 스냅샷이 모두 가장 최근 로컬 회차 이후에 수집됐으면 True.

    하나라도 읽을 수 없거나 회차 전 것이거나 시계 오류로 미래면 False —
    폴백이 도는 쪽으로 기운다. 판정 불능을 '신선'으로 보면 로컬이 죽었을 때
    아무도 수집하지 않는다.
    """
    now = now or datetime.now(KST)
    directory = ohlcv_dir or OHLCV_DIR
    for market in (['kr', 'us'] if markets == 'all' else [markets]):
        slot = last_local_slot(market, now)
        for name in MARKET_FILES[market]:
            fetched = _fetched_at(_read(name, directory, ref))
            if fetched is None or fetched < slot or fetched > now + FUTURE_TOLERANCE:
                return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--markets', choices=['kr', 'us', 'all'], default='all')
    ap.add_argument('--ref', default=None)
    args = ap.parse_args()

    fresh = is_fresh(args.markets, ref=args.ref)
    line = f'fresh={"true" if fresh else "false"}'
    src = f'ref={args.ref}' if args.ref else '작업본'
    print(f'[{args.markets}] {line} ({src} 기준, 로컬 회차 대비)')

    github_output = os.environ.get('GITHUB_OUTPUT')
    if github_output:
        with open(github_output, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
