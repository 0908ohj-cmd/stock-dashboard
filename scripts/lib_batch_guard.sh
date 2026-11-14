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
