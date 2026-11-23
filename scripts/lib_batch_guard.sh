# 배치 래퍼 보조 함수 — run_batch_local.sh가 source 한다. 단독 실행하지 않는다.
# 셸 테스트는 tests/test_lib_batch_guard.py.

# plist_drift <설치된 plist> <repo plist>
#   설치된 launchd plist가 repo 것과 같은지 본다. 래퍼 스크립트는 reset --hard로 자동 갱신되지만
#   plist는 install_batch_launchd.sh로 재설치해야 반영된다 — c0dd7bc(10-19)에서 repo plist를
#   17:30으로 옮겼는데 KR 배치는 10-20~11-05 내내 16:14에 돌았고 아무도 몰랐다 (#39 M21).
#   반환: 0 같음 / 1 다름 / 2 설치본 없음 / 3 repo 본 없음. 사유는 표준출력으로.
plist_drift() {
    local installed="$1" repo="$2"
    [ -f "$repo" ] || { echo "repo plist 없음: $repo"; return 3; }
    [ -f "$installed" ] || {
        echo "설치된 plist 없음: $installed — install_batch_launchd.sh로 등록할 것"; return 2; }
    cmp -s "$installed" "$repo" && return 0
    echo "설치된 plist가 repo와 다르다($installed) — install_batch_launchd.sh로 재설치할 것"
    return 1
}

# ── 배치 락 (#39 L3) ────────────────────────────────────────
# macOS에는 flock이 없어 락 디렉터리 + pid 파일의 배타적 생성(noclobber = O_EXCL)을 쓴다. 예전 구현의 구멍:
#   ① 건너뛸 때 알림이 없었다 (호출부가 LOCK_MSG를 notify한다)
#   ② mkdir과 pid 기록 사이에 다른 배치가 빈 pid를 '죽은 락'으로 보고 회수해 둘이 같이 돌았다
#      → pid 없는(또는 아직 빈) 락은 LOCK_PID_GRACE_SEC 동안 '잡는 중'으로 본다
#   ③ 늦게 끝난 배치의 trap이 그 사이 다른 배치가 잡은 락을 지웠다 → release_lock은 내 pid일 때만
#   ④ pid가 재사용되거나 배치가 멈추면 락이 영원히 '살아 있음' → 락 나이 상한
#   ⑤ mkdir 명령의 성공만 믿었다 — Ubuntu 26.04 기본 uutils mkdir 0.10은 동시에 실행하면 둘 다 0을
#      돌려준다(2026-11-23 CI 측정 600회 중 122회, 맥 /bin/mkdir은 0회) → pid 파일을 먼저 만든 쪽만 주인
LOCK_PID_GRACE_SEC="${LOCK_PID_GRACE_SEC:-60}"

_lock_age() {   # 락 디렉터리 나이(초). 못 읽으면 0 — 젊은 락으로 보아 회수하지 않는 쪽으로 기운다
    local m
    m="$(stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null)"
    case "$m" in ''|*[!0-9]*) echo 0; return ;; esac
    echo $(( $(date +%s) - m ))
}

_write_lock_pid() {   # noclobber(O_EXCL)로 pid 파일을 만든 쪽만 성공한다. 생성~기록 사이의 빈 파일은 '잡는 중'
    ( set -C; echo "$$" > "$1/pid" ) 2>/dev/null
}

# acquire_lock <락 디렉터리> <락 나이 상한(초)>
#   반환: 0 획득 / 1 다른 배치가 실행 중이라 건너뜀 / 2 획득 실패.
#   LOCK_MSG: 건너뛴 사유, 또는 죽은 락을 회수하고 획득했을 때 그 사유(아니면 빈 값).
acquire_lock() {
    local dir="$1" max_age="$2" pid age reason stale
    LOCK_MSG=""
    if mkdir "$dir" 2>/dev/null; then
        _write_lock_pid "$dir" && return 0
        [ -e "$dir/pid" ] || return 2   # pid가 있으면 경합에 진 것 — 아래에서 주인을 판정한다
    fi
    pid="$(cat "$dir/pid" 2>/dev/null || true)"
    age="$(_lock_age "$dir")"
    if [ -z "$pid" ]; then
        if [ "$age" -lt "$LOCK_PID_GRACE_SEC" ]; then
            LOCK_MSG="다른 배치가 락을 잡는 중(pid 기록 전, ${age}초) — 건너뜀"
            return 1
        fi
        reason="pid 기록 없음, ${age}초 경과"
    elif kill -0 "$pid" 2>/dev/null; then
        if [ "$age" -lt "$max_age" ]; then
            LOCK_MSG="다른 배치가 실행 중(pid $pid, $((age / 60))분째) — 건너뜀"
            return 1
        fi
        reason="pid $pid 살아 있으나 락 나이 $((age / 60))분 ≥ 상한 $((max_age / 60))분"
    else
        reason="pid $pid 종료됨"
    fi
    # 회수: rename은 원자적이라 동시에 회수하려는 배치 중 하나만 성공한다. 옮긴 락의 pid가 처음 본 것과
    # 다르면 그 사이 다른 배치가 새로 잡은 락이다 — 되돌리고 건너뛴다
    stale="$dir.stale.$$"
    rm -rf "$stale"
    if ! mv "$dir" "$stale" 2>/dev/null; then
        LOCK_MSG="죽은 락을 다른 배치가 먼저 회수함 — 건너뜀"
        return 1
    fi
    if [ "$(cat "$stale/pid" 2>/dev/null || true)" != "$pid" ]; then
        [ -e "$dir" ] || mv "$stale" "$dir" 2>/dev/null
        rm -rf "$stale"
        LOCK_MSG="다른 배치가 방금 락을 새로 잡음 — 건너뜀"
        return 1
    fi
    rm -rf "$stale"
    if ! mkdir "$dir" 2>/dev/null; then
        LOCK_MSG="죽은 락 회수 직후 다른 배치가 먼저 잡음 — 건너뜀"
        return 1
    fi
    if ! _write_lock_pid "$dir"; then
        [ -e "$dir/pid" ] || return 2
        LOCK_MSG="죽은 락 회수 직후 다른 배치가 먼저 잡음 — 건너뜀"
        return 1
    fi
    LOCK_MSG="락 회수($reason)"
    return 0
}

# release_lock <락 디렉터리> — 내 pid가 적힌 락만 지운다 (상한 초과로 회수된 뒤 늦게 끝난 배치가
# 새 배치의 락을 지우지 않게). 항상 0.
release_lock() {
    [ "$(cat "$1/pid" 2>/dev/null)" = "$$" ] && rm -rf "$1"
    return 0
}
