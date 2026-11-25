#!/usr/bin/env bash
# Item 1 matrix: build x EIO shim x shaping, two 10 s runs each.
cd "$(dirname "$0")"
for spec in "master 1 100mbit" "gso-unfixed 1 100mbit" "fix 1 100mbit" "master 0 \"\"" "gso-unfixed 0 \"\"" "fix 0 \"\""; do
  eval set -- $spec
  ./pair.sh up "${BENCH_BIN_DIR:?Set BENCH_BIN_DIR to the build directory}"/ray-$1 "$2" "$3" >/dev/null 2>&1 || { echo "$1 eio=$2 rate=$3 setup failed"; continue; }
  for i in 1 2; do echo "$1 eio=$2 rate=${3:-none} run$i $(./tput.sh 10)"; done
  path=$(docker exec rb-a ray status --json | jq -r '.networks[0].peers[0].connection.conn_type')
  halts=$(docker exec rb-a grep -c 'halting segmentation' /var/log/ray.log)
  echo "   path=$path halting_logs=$halts"
done
./pair.sh down
