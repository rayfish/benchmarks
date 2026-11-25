#!/usr/bin/env bash
# loadping.sh: TCP iperf3 a->b for 12 s with 100 pps ping a->b; prints Mbit/s and ping p50/p99.
cd "$(dirname "$0")"
B="$(cat /tmp/rb-bvpn)"
docker exec rb-b sh -c 'pkill -x iperf3; true'; docker exec rb-b iperf3 -s -D -1
sleep 0.3
docker exec rb-a iperf3 -c "$B" -t 12 -J > /tmp/lp-iperf.json &
sleep 2
docker exec rb-a ping -n -i 0.01 -c 800 "$B" > /tmp/lp-ping.txt
wait
mbps=$(jq -r '.end.sum_received.bits_per_second/1e6|floor' /tmp/lp-iperf.json)
sed -n 's/.*time=\([0-9.]*\) ms.*/\1/p' /tmp/lp-ping.txt | sort -n | awk -v m="$mbps" '{a[NR]=$1} END{printf "mbps=%s ping_p50=%s p99=%s max=%s n=%d\n", m, a[int(NR*0.5)], a[int(NR*0.99)], a[NR], NR}'
