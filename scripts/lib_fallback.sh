# GitHub 폴백 즉시 실행 — run_batch_local.sh가 source 한다.
#
# 맥 배치가 이번 회차를 못 채우면 예약 폴백을 기다리지 않고 workflow_dispatch로 바로 띄운다.
# 예약 cron은 실측 3~8시간 늦게 뜬다(2026-10~11: KR 18:10 예약 → 00:17~04:43,
# US 08:50 예약 → 11:00~13:49). 수동 실행은 1분 안에 시작되고, fetch-data.yml은
# 수동 실행에 게이트를 걸지 않으므로 바로 수집한다.
# 맥이 꺼져 있으면 이 경로는 못 쓴다 — 그때를 위해 예약 cron은 마지막 안전망으로 남긴다.
#
# 인증: gh CLI 자신의 로그인(키체인)을 쓴다. 워크플로 실행에는 레포 쓰기 권한이 필요하다.

FALLBACK_WORKFLOW="fetch-data.yml"
FALLBACK_REPO="0908ohj-cmd/stock-dashboard"

_fallback_gh() {
    if [ -n "${GH_BIN:-}" ]; then echo "$GH_BIN"; return 0; fi
    command -v gh 2>/dev/null && return 0
    # launchd의 PATH에는 Homebrew가 없다
    [ -x /opt/homebrew/bin/gh ] && { echo /opt/homebrew/bin/gh; return 0; }
    return 1
}

# 사용: request_fallback <kr|us>
# 성공 0, 실패 1. gh 출력은 표준출력으로 내보내 호출부가 로그에 남긴다.
request_fallback() {
    local market="$1" gh_bin
    case "$market" in
        kr|us) ;;
        *) echo "알 수 없는 시장: $market"; return 1 ;;
    esac
    gh_bin="$(_fallback_gh)" || { echo "gh CLI 없음"; return 1; }
    "$gh_bin" workflow run "$FALLBACK_WORKFLOW" --repo "$FALLBACK_REPO" --ref main \
        -f markets="$market" 2>&1
}

# 사용: round_is_fresh <python> <kr|us> [repo_root]
# 올린 결과가 이번 회차를 채웠는지 폴백 게이트(check_snapshot_fresh.py)와 같은 기준으로
# 판정한다 — 회차 시각 이후 수집 + 종목이 같은 시장 지수보다 늦지 않음.
# fresh=true면 0, fresh=false면 1, 판정 불가(게이트 오류)면 2. 게이트 출력은 표준출력으로.
# 판정 불가를 미달(1)로 치지 않는다 — 게이트 버그 하나로 매일 KR 폴백이 돌면
# GitHub 러너에서 KRX 조회가 그만큼 늘어난다(2026-08 KRX IP 차단 전례).
round_is_fresh() {
    local python="$1" market="$2" root="${3:-.}" out
    out="$("$python" "$root/scripts/check_snapshot_fresh.py" --markets "$market" 2>&1)"
    echo "$out"
    case "$out" in
        *"fresh=true"*) return 0 ;;
        *"fresh=false"*) return 1 ;;
        *) return 2 ;;
    esac
}
