#!/bin/bash
# launchd 에이전트 설치·재등록. 배치 전용 체크아웃에서 실행한다.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/launchd" && pwd)"
DEST="$HOME/Library/LaunchAgents"
RUNNER="/Users/ygun/Workspace/stock-dashboard-batch/scripts/run_batch_local.sh"

[ -x "$RUNNER" ] || { echo "래퍼가 없거나 실행 권한이 없다: $RUNNER" >&2; exit 1; }

mkdir -p "$DEST"
for label in com.stockdashboard.batch.kr com.stockdashboard.batch.us; do
    cp "$SRC/$label.plist" "$DEST/$label.plist"
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$DEST/$label.plist"
    echo "등록: $label"
done

echo "--- 등록 확인 ---"
launchctl list | grep stockdashboard || echo "(목록에 없음 — 확인 필요)"
