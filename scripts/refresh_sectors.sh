#!/usr/bin/env bash
# 섹터(테마) 캐시 원커맨드 갱신: pull → 분류 → 변경 시에만 커밋·push.
# 무인자 = --stale-days 7. 인자를 주면 그대로 refresh_sectors.py에 전달 (예: --all).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# 중복 실행 방지 (bg job끼리 /tmp 공유 문제를 피해 repo 안에 둔다)
LOCK="$REPO/.refresh_sectors.lock"
if [ -e "$LOCK" ]; then
    echo "이미 실행 중 ($LOCK 존재) — 중단" >&2
    exit 1
fi
trap 'rm -f "$LOCK"' EXIT
touch "$LOCK"

command -v claude >/dev/null 2>&1 || {
    echo "claude CLI 없음 — 인증된 로컬 머신에서 실행하세요" >&2
    exit 1
}

git pull --ff-only

PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="python3"

if [ $# -eq 0 ]; then
    "$PY" scripts/refresh_sectors.py --stale-days 7
else
    "$PY" scripts/refresh_sectors.py "$@"
fi

# 변경 감지: theme_cache.json은 첫 실행 때 미추적(untracked)일 수 있어 status로 본다
if [ -z "$(git status --porcelain -- theme_cache.json data/themes.json)" ]; then
    echo "캐시 변경 없음 — 커밋 생략"
    exit 0
fi

N="$("$PY" -c "import json; print(len(json.load(open('theme_cache.json'))))")"
git add theme_cache.json data/themes.json
git commit -m "data: 섹터 캐시 갱신 (${N}종목)"
git push || { git pull --rebase && git push; }
echo "완료 — ${N}종목 push됨"
