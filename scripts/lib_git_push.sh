# run_batch_local.sh가 source하는 push 헬퍼. 단독 실행하지 않는다.
#
# 배치는 git fetch·reset 후 3분 반쯤 수집한 뒤 push한다. 그 사이 원격이 앞서
# 나갈 수 있다 — stockEdge의 리더보드 커밋이 이 저장소에 수시로 들어온다.
# 2026-09 실측으로 US 리더보드는 07:01~14:12 사이 날마다 다른 시각에 왔고,
# 당시 07:00이던 US 배치와는 07:01~07:05에 정면으로 겹쳤다. 배치를 09:00으로
# 옮긴 지금도 시각이 고정돼 있지 않아 겹칠 수 있다. rebase 없이 push하면
# non-fast-forward로 거절돼 배치가 실패로 끝난다.
#
# 리더보드는 data/leaderboard/만, 배치는 data/ohlcv/와 *_10ema.tickers만
# 건드려 파일이 겹치지 않으므로 평소 rebase는 충돌 없이 끝난다.

# push_with_rebase [credential.helper 값]
#   원격 위로 rebase한 뒤 push한다. push가 거절되면(그 사이 또 앞서 나감)
#   최대 3회까지 다시 rebase·push한다.
#   반환: 0 성공 / 1 rebase 실패(충돌 등 — rebase는 abort해 둔다) /
#         2 3회 시도 후에도 push 거절
#
#   credential.helper 값은 인자로 받아 `git -c`로만 넘긴다. 토큰은 그 값 안의
#   `$DASHBOARD_GITHUB_TOKEN` 문자열로 들어가고, git이 헬퍼를 실행할 때 환경에서
#   펼친다 — 그래서 토큰 자체는 argv에도 .git/config에도 남지 않는다.
push_with_rebase() {
    local cred="${1:-}"
    local -a gitc=(git)
    if [ -n "$cred" ]; then
        gitc=(git -c "credential.helper=$cred")
    fi

    local attempt
    for attempt in 1 2 3; do
        if ! "${gitc[@]}" pull --rebase --autostash --quiet origin main; then
            # rebase 진행 상태를 남기면 다음 날 배치의 pull --rebase가 전부 막힌다
            git rebase --abort 2>/dev/null
            return 1
        fi
        if "${gitc[@]}" push --quiet origin main; then
            return 0
        fi
        sleep "${PUSH_RETRY_SLEEP:-5}"
    done
    return 2
}
