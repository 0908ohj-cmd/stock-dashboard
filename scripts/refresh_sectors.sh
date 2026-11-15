#!/usr/bin/env bash
# 구 섹터(테마) 캐시 원커맨드 갱신: pull → 분류 → 변경 시에만 커밋·push.
# 무인자 = 미캐시 종목만. 인자는 그대로 refresh_sectors.py에 전달 (예: --all, --stale-days 7).
# 예전 무인자 기본(--stale-days 7)은 캐시 전량이 대상이 되어 LLM WebSearch를 1400회 넘게 불렀다(#45 M19).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# 중복 실행 방지 (bg job끼리 /tmp 공유 문제를 피해 repo 안에 둔다).
# mkdir은 확인과 생성이 한 번에 일어나 원자적이다 — [ -e ] 뒤 touch는 그 사이에 둘이 들어올 수 있다
LOCK="$REPO/.refresh_sectors.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
    echo "이미 실행 중 ($LOCK 존재) — 중단" >&2
    exit 1
fi
trap 'rmdir "$LOCK"' EXIT

command -v claude >/dev/null 2>&1 || {
    echo "claude CLI 없음 — 인증된 로컬 머신에서 실행하세요" >&2
    exit 1
}

git pull --ff-only

PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="python3"

"$PY" scripts/refresh_sectors.py "$@"

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
