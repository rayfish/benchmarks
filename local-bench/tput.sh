#!/usr/bin/env bash
# tput.sh <seconds> [extra iperf3 args]: TCP a->b over the mesh; prints Mbit/s and retransmits.
set -euo pipefail
T="${1:-10}"; shift || true
B="$(cat /tmp/rb-bvpn)"
docker exec rb-b pkill iperf3 2>/dev/null || true
docker exec -d rb-b iperf3 -s -1
sleep 0.5
docker exec rb-a timeout $((T+20)) iperf3 -c "$B" -t "$T" -J "$@" > /tmp/rb-iperf.json || true
jq -r '"mbps=\(.end.sum_received.bits_per_second/1e6|floor) retr=\(.end.sum_sent.retransmits) err=\(.error // "")"' /tmp/rb-iperf.json 2>/dev/null || tail -3 /tmp/rb-iperf.json
