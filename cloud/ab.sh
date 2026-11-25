#!/usr/bin/env bash
# Compare two Rayfish checkouts on the same provisioned guests.
set -euo pipefail
dir="$(cd "$(dirname "$0")" && pwd)"
if [[ $# != 2 || ! -f "${RAY_BENCH_SERVERS:-$dir/.servers}" ]]; then
    echo "Usage: cloud/ab.sh BASELINE_CHECKOUT CANDIDATE_CHECKOUT (provision the pair first)" >&2
    exit 2
fi
export ITERATIONS="${ITERATIONS:-5}" DURATION="${DURATION:-10}"
for label in baseline candidate; do
    checkout="$(cd "$1" && pwd)"
    shift
    result="$(mktemp "$dir/ab-$label.XXXXXX.tmp")"
    if RAYFISH_SOURCE="$checkout" KEEP_STATE=0 bash "$dir/run.sh" | tee "$result"; then
        report="$(sed -n 's/^Saved: //p' "$result" | head -1)"
        [[ -f "$report" ]]
        cp -- "$report" "$dir/results/AB-$label.md"
        rm -- "$result"
    else
        exit 1
    fi
done
