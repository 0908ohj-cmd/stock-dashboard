#!/usr/bin/env python3
"""두 테마 분류 파일(data/classification/*.json)을 종목 단위로 병합한다 — 배치 push 경합용.

배치가 도는 사이 앱(주도섹터 탭 > 분류 검토)이 사용자 확정을 커밋하면, 배치 push의 충돌
재구성(lib_git_push.sh)이 파일을 통째로 "우리 것"으로 덮어 그 확정이 사라졌다(#33).
병합 규칙은 data.classification.merge_stores 한 곳에 있다.

결과 기록이 원격(THEIRS)과 같으면 원격 파일을 바이트 그대로 쓴다 — 새 커밋이 생기지 않는다.

사용법: python3 scripts/merge_classification.py OURS THEIRS OUT
        (OUT이 THEIRS와 같은 경로여도 된다 — 둘 다 읽은 뒤에 쓴다)
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data import classification  # noqa: E402


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print('사용법: merge_classification.py OURS THEIRS OUT', file=sys.stderr)
        return 2
    ours_p, theirs_p, out_p = (pathlib.Path(p) for p in argv)
    theirs_text = theirs_p.read_text(encoding='utf-8')
    ours, theirs = json.loads(ours_p.read_text(encoding='utf-8')), json.loads(theirs_text)

    merged = classification.merge_stores(ours.get('records', {}), theirs.get('records', {}))
    if merged == theirs.get('records', {}):
        out_p.write_text(theirs_text, encoding='utf-8')
    else:
        body = {**theirs, **ours,
                'updatedAt': max(ours.get('updatedAt') or '', theirs.get('updatedAt') or ''),
                'records': merged}
        out_p.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'[merge] {out_p.name}: {len(merged)}종목 병합')
    return 0


if __name__ == '__main__':
    sys.exit(main())
