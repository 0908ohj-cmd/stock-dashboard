# 배치 커밋을 원격에 올리는 헬퍼. run_batch_local.sh와 Action 폴백이 source한다.
# 단독 실행하지 않는다.
#
# 왜 필요한가 — 멱등성
#   로컬 배치(1순위)와 Action 폴백은 서로 다른 기계라 공유 락이 없다. 로컬이 늦게
#   돌아 Action이 먼저 같은 날 스냅샷을 push하면, 뒤이어 올리는 쪽의 push는
#   non-fast-forward로 거절된다. 여기에 git rebase를 쓰면 안 된다 —
#   data/ohlcv/*.json은 줄바꿈 없는 한 줄 JSON이라, 두 쪽이 같은 파일을 조금이라도
#   다르게 쓰면 파일 전체가 충돌한다. 특히 indices.json은 KOSPI·KOSDAQ·NASDAQ이
#   한 파일에 있어, KR 배치와 US 배치가 겹치면 한쪽을 통째로 고르는 순간 다른
#   시장의 지수 갱신이 사라진다.
#
#   그래서 원격이 앞서 있으면 원격을 그대로 받은 뒤(reset), 우리가 바꾼 파일을
#   파일 종류별로 다시 얹는다.
#     - data/ohlcv/*.json → merge_snapshots.py로 종목 단위 병합
#     - 그 밖(티커 목록 등) → 우리 것을 쓴다. 같은 날 같은 알고리즘으로 만든 값이라
#       사실상 같고, 병합할 수 없는 형식이다
#   결과가 원격과 같으면 새 커밋 없이 끝난다. 몇 번을 다시 돌려도 결과가 같다.
#
#   stockEdge의 리더보드 커밋(data/leaderboard/)도 불규칙한 시각에 들어오는데
#   (2026-09 실측 07:01~14:12), 파일이 겹치지 않으므로 같은 경로로 그대로 보존된다.

_LIB_GIT_PUSH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# _rebuild_on_remote
#   현재 HEAD의 변경을 origin/main 위에 다시 얹는다. 반환 0 성공 / 1 병합 실패.
_rebuild_on_remote() {
    local orig base tmp f
    local py="${PYTHON:-python3}"
    orig="$(git rev-parse HEAD)" || return 1
    base="$(git merge-base HEAD origin/main)" || return 1
    tmp="$(mktemp -d)" || return 1

    local files
    files="$(git diff --name-only "$base" "$orig")"

    # reset 전에 우리 버전을 떠 둔다
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        mkdir -p "$tmp/$(dirname "$f")"
        git show "$orig:$f" > "$tmp/$f" 2>/dev/null || rm -f "$tmp/$f"
    done <<< "$files"

    git reset --hard --quiet origin/main || { rm -rf "$tmp"; return 1; }

    while IFS= read -r f; do
        [ -n "$f" ] || continue
        [ -f "$tmp/$f" ] || continue
        mkdir -p "$(dirname "$f")"
        case "$f" in
            data/ohlcv/*.json)
                if [ -f "$f" ]; then
                    "$py" "$_LIB_GIT_PUSH_DIR/merge_snapshots.py" "$tmp/$f" "$f" "$f" \
                        || { rm -rf "$tmp"; return 1; }
                else
                    cp "$tmp/$f" "$f"
                fi
                ;;
            *)
                cp "$tmp/$f" "$f"
                ;;
        esac
        git add -- "$f"
    done <<< "$files"
    rm -rf "$tmp"

    # 원격이 이미 같은 내용을 갖고 있으면 올릴 것이 없다
    if git diff --cached --quiet; then
        return 0
    fi
    # 원래 커밋의 메시지·작성자를 그대로 쓴다 (commit -C). 커미터도 원래 것으로.
    GIT_COMMITTER_NAME="$(git log -1 --format=%cn "$orig")" \
    GIT_COMMITTER_EMAIL="$(git log -1 --format=%ce "$orig")" \
        git commit --quiet -C "$orig"
}

# sync_and_push [credential.helper 값]
#   원격이 앞서 있으면 데이터 수준으로 합친 뒤 push한다. 그 사이 또 앞서 나가면
#   최대 3회까지 다시 합친다.
#   반환: 0 성공(올릴 것이 없었던 경우 포함) / 1 fetch·병합 실패 / 2 3회 후에도 push 거절
#
#   credential.helper 값은 인자로 받아 `git -c`로만 넘긴다. 토큰은 그 값 안의
#   `$DASHBOARD_GITHUB_TOKEN` 문자열로 들어가고, git이 헬퍼를 실행할 때 환경에서
#   펼친다 — 그래서 토큰 자체는 argv에도 .git/config에도 남지 않는다.
sync_and_push() {
    local cred="${1:-}"
    local -a gitc=(git)
    if [ -n "$cred" ]; then
        gitc=(git -c "credential.helper=$cred")
    fi

    local attempt
    for attempt in 1 2 3; do
        "${gitc[@]}" fetch --quiet origin main || return 1
        if ! git merge-base --is-ancestor origin/main HEAD; then
            _rebuild_on_remote || return 1
        fi
        if "${gitc[@]}" push --quiet origin HEAD:main; then
            return 0
        fi
        sleep "${PUSH_RETRY_SLEEP:-5}"
    done
    return 2
}
