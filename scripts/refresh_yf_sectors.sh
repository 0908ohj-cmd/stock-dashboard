#!/usr/bin/env bash
# yfinance 대분류(섹터) 캐시 원커맨드 수집.
#
# .info는 레이트 리밋이 빡빡해 한 번에 전량이 안 들어온다(8워커 전량 시도 시 45% 실패). 프로브로
# 리밋 해제를 감지해 미수집분만 이어 받는 구조로 돌린다 — 캐시 우선이라 중단·재개가 안전하다.
# 무인자 = 미수집분만, 인자는 refresh_yf_sectors.py로 그대로 전달(예: --all).
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

LOCK="$REPO/.refresh_yf_sectors.lock"
if [ -e "$LOCK" ]; then
    echo "이미 실행 중 ($LOCK 존재) — 중단" >&2
    exit 1
fi
trap 'rm -f "$LOCK"' EXIT
touch "$LOCK"

PY="$REPO/venv/bin/python"
[ -x "$PY" ] || PY="/Users/ygun/Workspace/stock-dashboard/venv/bin/python"
[ -x "$PY" ] || PY="python3"

prev_fail=-1
for i in $(seq 1 30); do
    # 프로브 — 리밋에 걸려 있으면 10분 대기 후 재시도
    if ! "$PY" -c "
import sys, yfinance as yf
try:
    info = yf.Ticker('AAPL').info
    sys.exit(0 if info.get('industry') else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1; then
        echo "[probe] round $i: yfinance 레이트 리밋 — 10분 대기"
        sleep 600
        continue
    fi
    echo "[probe] round $i: 리밋 해제 — 미수집분 조회 시작"

    out=$("$PY" scripts/refresh_yf_sectors.py --workers 2 "$@" 2>&1)
    echo "$out"

    if echo "$out" | grep -q "수집할 종목 없음"; then
        echo "[done] 전량 수집 완료 (round $i)"
        exit 0
    fi
    fail=$(echo "$out" | grep -oE "⚠️ 조회 실패 [0-9]+종목" | grep -oE "[0-9]+" || true)
    if [ -z "${fail:-}" ]; then
        echo "[done] 실패 종목 없음 (round $i)"
        exit 0
    fi
    # 상장폐지·표기오류(BRKB 등 TradingView 표기) 종목은 몇 번을 돌려도 안 잡힌다.
    # 실패 수가 줄지 않으면 남은 건 구조적 미조회분이므로 멈춘다.
    if [ "$fail" -ge "$prev_fail" ] && [ "$prev_fail" -ge 0 ]; then
        echo "[done] 진전 없음 (실패 ${fail}종목 고정) — 구조적 미조회분으로 판단, 종료"
        exit 0
    fi
    prev_fail=$fail
    echo "[round $i] 실패 ${fail}종목 — 다음 라운드 전 5분 대기"
    sleep 300
done
echo "[stop] 최대 라운드 도달 — 수동 확인 필요"
exit 1
