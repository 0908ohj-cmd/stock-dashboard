#!/usr/bin/env bash
# 진단 전용 (머지 안 함): 8개 동시 acquire_lock을 ROUNDS회 반복, 주인이 1이 아닌 회차의 프로세스별 xtrace 출력
LIB="$1"; ROUNDS="${2:-300}"; N=8; bad=0
for r in $(seq 1 "$ROUNDS"); do
  d=$(mktemp -d); mkdir "$d/rc" "$d/tr"
  for i in $(seq 1 $N); do
    bash -c '. "$1"; d="$2"; N="$3"
      while [ ! -e "$d/go" ]; do sleep 0.01; done
      exec 9>"$d/tr/$$"; BASH_XTRACEFD=9; PS4="+ \${EPOCHREALTIME} [$$] "
      set -x; acquire_lock "$d/batch.lock" 3600; rc=$?; set +x
      echo "$rc|$$|$LOCK_MSG" > "$d/rc/rc.$$"
      if [ $rc -eq 0 ]; then for _ in $(seq 1 1500); do [ "$(ls "$d/rc" | wc -l)" -ge "$N" ] && break; sleep 0.01; done; fi' _ "$LIB" "$d" "$N" &
  done
  touch "$d/go"; wait
  owners=$(cat "$d"/rc/* | grep -c '^0|')
  if [ "$owners" -ne 1 ]; then
    bad=$((bad+1)); echo "=================== round $r owners=$owners"; cat "$d"/rc/*
    echo "--- merged trace (time-sorted)"; cat "$d"/tr/* | sort -t' ' -k2,2n | cut -c1-200
    ls -la "$d" "$d"/batch.lock* 2>&1 | head -20
  fi
  rm -rf "$d"
done
echo "RESULT bad=$bad/$ROUNDS"
