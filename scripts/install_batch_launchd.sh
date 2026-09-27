#!/bin/bash
# launchd 에이전트 설치·재등록.
#
# 최초 셋업 (배치 전용 체크아웃이 없을 때):
#   git clone https://github.com/0908ohj-cmd/stock-dashboard.git \
#       /Users/ygun/Workspace/stock-dashboard-batch
#   cd /Users/ygun/Workspace/stock-dashboard-batch
#   bash scripts/install_batch_launchd.sh
#
# 배치 전용 체크아웃을 쓰는 이유: 메인 체크아웃은 여러 세션이 브랜치를 바꾸므로
# 거기서 run_batch_local.sh가 reset --hard를 하면 작업이 통째로 날아간다.
# run_batch_local.sh 자체도 $REPO가 이 경로가 아니면 실행을 거부한다.
#
# GitHub Action(.github/workflows/fetch-data.yml)이 폴백으로 남아 있다 —
# KR 20:00 / US 13:00 KST에 깨어나, 로컬이 이미 수집했으면 그대로 빠지고
# 안 했으면 대신 시세를 채운다. 즉 여기 등록을 빠뜨려도 데이터가 끊기지는
# 않지만, 수집이 몇 시간 늦어지고 10EMA 유니버스는 갱신되지 않는다
# (러너에 KRX 로그인이 없다). 폴백이 일한 날은 커밋 메시지에 "(Action 폴백)"
# 이 붙으므로, 그게 계속 보이면 맥 배치가 죽은 것이다.
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
