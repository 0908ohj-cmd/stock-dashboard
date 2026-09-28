#!/usr/bin/env python3
"""두 스냅샷 파일을 데이터 수준에서 병합한다 — 배치 push 경합의 해결사.

로컬 배치와 Action 폴백이 같은 날 같은 시장을 각자 수집해 push가 엇갈리면,
data/ohlcv/*.json은 한 줄짜리 JSON이라 git이 파일 전체를 충돌로 본다.
git 수준에서 한쪽을 고르면 다른 쪽이 가진 것이 사라진다 — 특히 indices.json은
KOSPI·KOSDAQ·NASDAQ이 한 파일에 있어, KR 배치와 US 배치가 동시에 쓰면
통째 선택이 다른 시장의 지수 갱신을 지운다.

그래서 종목(지수) 단위로 합친다.
  - 한쪽에만 있는 종목은 그대로 남긴다
  - 양쪽에 있으면 store._safe_merge로 날짜 기준 병합한다. 겹치는 날짜는
    나중에 수집한(fetched_at이 늦은) 쪽을 채택한다 — 수정주가가 더 확정된 값이다
  - fetched_at·last_trading_date는 둘 중 늦은 것

사용법: python3 scripts/merge_snapshots.py OURS THEIRS OUT
        (OUT이 THEIRS와 같은 경로여도 된다 — 둘 다 읽은 뒤에 쓴다)
시장 이름은 파일명(KR_KOSPI.json → KR_KOSPI)에서 얻는다.
"""
import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import store  # noqa: E402


def _fetched(snap: dict) -> datetime:
    try:
        return datetime.fromisoformat(snap['fetched_at'])
    except (KeyError, TypeError, ValueError):
        return datetime.min.replace(tzinfo=store.KST)


def merge_snapshot(a: dict, b: dict, market: str) -> dict:
    """두 스냅샷을 합친 새 스냅샷. 인자 순서와 무관하게 같은 결과를 낸다."""
    newer, older = (a, b) if _fetched(a) >= _fetched(b) else (b, a)
    window = store.INDEX_DAYS if market == 'indices' else store.STOCK_DAYS

    new_data, old_data = newer.get('data', {}), older.get('data', {})
    data = {}
    for key in sorted(set(new_data) | set(old_data)):
        n, o = new_data.get(key), old_data.get(key)
        if n is None:
            data[key] = o
        elif o is None:
            data[key] = n
        else:
            df = store._safe_merge(store._records_to_df(n), o, window,
                                   f'{market}:{key}')
            data[key] = store._df_to_records(df)

    trading_dates = [s.get('last_trading_date') for s in (a, b) if s.get('last_trading_date')]
    failed = list(newer.get('failed', []))
    return {
        **older, **newer,
        'fetched_at': newer.get('fetched_at'),
        'last_trading_date': max(trading_dates) if trading_dates else None,
        # 성공 종목 수 — build_market_snapshot과 같은 의미(실패했어도 옛 이력이
        # 보존된 종목은 data에 있지만 성공으로 세지 않는다)
        'ticker_count': len(data) - len(set(failed) & set(data)),
        'failed': failed,
        'data': data,
    }


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print('사용법: merge_snapshots.py OURS THEIRS OUT', file=sys.stderr)
        return 2
    ours_p, theirs_p, out_p = (pathlib.Path(p) for p in argv)
    ours = json.loads(ours_p.read_text(encoding='utf-8'))
    theirs = json.loads(theirs_p.read_text(encoding='utf-8'))
    market = theirs.get('market') or ours.get('market') or out_p.stem

    merged = merge_snapshot(ours, theirs, market)
    out_p.write_text(json.dumps(merged, ensure_ascii=False), encoding='utf-8')
    print(f'[merge] {market}: {len(merged["data"])}종목 병합 '
          f'(fetched_at={merged["fetched_at"]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
