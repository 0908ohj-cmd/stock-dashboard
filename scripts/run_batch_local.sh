#!/bin/bash
# 스냅샷 배치 로컬 실행 — launchd가 호출한다.
#
#   사용법: run_batch_local.sh <kr|us> [--dry-run]
#
# 배치 전용 체크아웃(main 고정)에서만 돌린다. 메인 체크아웃은 여러 Claude
# 세션이 브랜치를 바꾸므로 거기서 reset --hard를 하면 작업이 통째로 날아간다.
# --dry-run은 reset/commit/push를 모두 건너뛰므로 개발 체크아웃에서도 안전하다.
set -uo pipefail

MARKET="${1:-}"
DRY_RUN="${2:-}"

case "$MARKET" in
    kr|us) ;;
    *) echo "사용법: $0 <kr|us> [--dry-run]" >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BATCH_REPO="/Users/ygun/Workspace/stock-dashboard-batch"
ENV_FILE="/Users/ygun/Workspace/stockEdge/.env"
LOG="$HOME/Library/Logs/stock-dashboard-batch.log"
LOCKDIR="/tmp/stock-dashboard-batch.lock"
PYTHON="/opt/homebrew/bin/python3"

# 로그에 KRX 로그인 ID 등이 남으므로 소유자만 읽게 만든다
umask 077

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] [$MARKET] $*" | tee -a "$LOG"; }

notify() {
    # 조용한 실패가 이 작업의 근본 원인이었다 — 실패는 반드시 알린다
    [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0
    curl -s -m 15 -o /dev/null \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=📉 stock-dashboard 배치 [$MARKET]: $1" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" || true
}

die() { log "FAIL: $1"; notify "실패 — $1"; exit 1; }

# notify()가 락 처리 시점에도 텔레그램 변수를 쓸 수 있도록 락보다 먼저 env를 읽는다.
[ -f "$ENV_FILE" ] || die "env 파일 없음: $ENV_FILE"
set -a; . "$ENV_FILE"; set +a

# 중복 실행 방지 (macOS에는 flock이 없어 mkdir 원자성을 쓴다)
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    # 락이 남아 있으면 주인이 살아 있는지 본다 — 죽은 락은 배치를 영구히 멈춘다
    stale_pid="$(cat "$LOCKDIR/pid" 2>/dev/null || echo '')"
    if [ -n "$stale_pid" ] && kill -0 "$stale_pid" 2>/dev/null; then
        log "다른 배치가 실행 중(pid $stale_pid) — 건너뜀"
        exit 0
    fi
    log "WARN: 죽은 락 회수 (pid=${stale_pid:-unknown})"
    notify "죽은 락을 회수하고 재시작했다 (pid=${stale_pid:-unknown})"
    rm -rf "$LOCKDIR"
    mkdir "$LOCKDIR" 2>/dev/null || die "락 획득 실패"
fi
echo $$ > "$LOCKDIR/pid"
trap 'rm -rf "$LOCKDIR" 2>/dev/null' EXIT

cd "$REPO" || die "체크아웃 없음: $REPO"
log "시작 (repo=$REPO)"

if [ "$DRY_RUN" = "--dry-run" ]; then
    log "dry-run — 저장소 동기화 생략, 현재 작업본 그대로 사용"
else
    [ -n "${DASHBOARD_GITHUB_TOKEN:-}" ] || die "DASHBOARD_GITHUB_TOKEN 없음"
    branch="$(git rev-parse --abbrev-ref HEAD)"
    [ "$branch" = "main" ] || die "main이 아닌 브랜치($branch) — 배치 전용 체크아웃에서만 실행할 것"
    [ "$REPO" = "$BATCH_REPO" ] || die "배치 전용 체크아웃이 아니다($REPO) — 실제 실행은 $BATCH_REPO 에서만 허용한다"
    git fetch origin --quiet || die "git fetch 실패"
    git reset --hard origin/main --quiet || die "git reset 실패"
fi

# 유니버스 빌드는 실패해도 스냅샷 수집을 막지 않는다.
# 2026-08-31~09-09 KRX 장애 때 이 단계가 죽으면서 시세 수집까지 3일 멈췄다.
if [ "$MARKET" = "kr" ]; then
    BUILDER="scripts/build_kr_10ema_universe.py"
else
    BUILDER="scripts/build_us_growth_universe.py"
fi
if ! "$PYTHON" "$BUILDER" >>"$LOG" 2>&1; then
    log "WARN: 유니버스 빌드 실패 — 스냅샷 수집은 계속 진행"
    notify "유니버스 빌드 실패 (스냅샷은 계속 진행)"
fi

"$PYTHON" scripts/fetch_snapshot.py --markets "$MARKET" >>"$LOG" 2>&1 \
    || die "스냅샷 수집 실패"

if [ "$DRY_RUN" = "--dry-run" ]; then
    log "dry-run 완료 — 변경된 데이터 파일:"
    git status --short -- data/ohlcv data/saved | tee -a "$LOG"
    exit 0
fi

git add data/ohlcv \
        data/saved/kospi_10ema.tickers \
        data/saved/kosdaq_10ema.tickers \
        data/saved/us_10ema.tickers \
    || die "git add 실패"
if git diff --cached --quiet; then
    log "변경 없음 — 종료"
    exit 0
fi

git -c user.name="stock-dashboard-batch" \
    -c user.email="batch@localhost" \
    commit -q -m "data: $MARKET 스냅샷 $(date '+%F')" || die "commit 실패"

# 수집하는 3분 반 사이 원격이 앞서 나갈 수 있어 rebase 후 push한다
# (lib_git_push.sh 참조 — stockEdge 리더보드 커밋이 날마다 다른 시각에 들어온다).
# 토큰을 리모트 URL에 박지 않는다 — .git/config에 비밀값이 남는다.
# 작은따옴표라 $DASHBOARD_GITHUB_TOKEN은 여기서 펼쳐지지 않고, git이 헬퍼를
# 실행할 때 환경에서 펼친다. 그래서 ps 출력에도 노출되지 않는다.
. "$REPO/scripts/lib_git_push.sh" || die "lib_git_push.sh 로드 실패"
CRED_HELPER='!f() { echo username=x-access-token; echo "password=$DASHBOARD_GITHUB_TOKEN"; }; f'
push_with_rebase "$CRED_HELPER"
case $? in
    0) ;;
    1) die "pull --rebase 실패 — 원격과 충돌했거나 fetch 불가 (rebase는 중단해 둠)" ;;
    *) die "push 실패 — 3회 재시도 후에도 거절" ;;
esac

log "완료"
