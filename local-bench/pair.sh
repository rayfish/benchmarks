#!/usr/bin/env bash
# Two Rayfish daemons in privileged containers on one Docker bridge.
#   pair.sh up <bin> [eio=0|1] [rate e.g. 100mbit|""] [extra env, e.g. RAYFISH_X=1]
#   pair.sh down
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NET=raybench
c(){ docker exec "$@"; }
down(){ docker rm -f rb-a rb-b >/dev/null 2>&1 || true; docker network rm $NET >/dev/null 2>&1 || true; }
up(){
  local bin="$1" eio="${2:-0}" rate="${3:-}" extra="${4:-}"
  down
  docker network create $NET >/dev/null
  for n in a b; do
    docker run -d --name rb-$n --hostname rb-$n --network $NET --privileged \
      --device /dev/net/tun --cpuset-cpus "$([[ $n == a ]] && echo ${CPUS_A:-0,1} || echo ${CPUS_B:-2,3})" \
      -v "$bin":/usr/local/bin/ray:ro -v "$HERE":/bench:ro \
      raybench:local sleep infinity >/dev/null
    if [[ -n "$rate" ]]; then
      c rb-$n tc qdisc add dev eth0 root handle 1: htb default 10
      c rb-$n tc class add dev eth0 parent 1: classid 1:10 htb rate "$rate" ceil "$rate"
      case "${LEAF:-netem}" in
        netem) c rb-$n tc qdisc add dev eth0 parent 1:10 handle 10: netem delay "${DELAY:-0}ms" limit 1000 ;;
        bfifo) c rb-$n tc qdisc add dev eth0 parent 1:10 handle 10: bfifo limit 1100000 ;;
        fq_codel) c rb-$n tc qdisc add dev eth0 parent 1:10 handle 10: fq_codel ;;
      esac
    fi
    local pre=""; [[ "$eio" == 1 ]] && pre="LD_PRELOAD=/bench/gso_eio.so"; [[ "$eio" == count ]] && pre="LD_PRELOAD=/bench/gso_count.so"
    c -d rb-$n sh -c "env $pre $extra RUST_LOG=info ray daemon > /var/log/ray.log 2>&1"
  done
  for _ in $(seq 50); do c rb-a ray status >/dev/null 2>&1 && c rb-b ray status >/dev/null 2>&1 && break; sleep 0.2; done
  if [[ -n "${ENGINE:-}" ]]; then
    for n in a b; do
      c rb-$n ray config set quic-engine "$ENGINE" >/dev/null
      c rb-$n pkill -x ray; sleep 1
      local pre=""; [[ "$eio" == 1 ]] && pre="LD_PRELOAD=/bench/gso_eio.so"; [[ "$eio" == count ]] && pre="LD_PRELOAD=/bench/gso_count.so"
      c -d rb-$n sh -c "env $pre $extra RUST_LOG=info ray daemon >> /var/log/ray.log 2>&1"
    done
    for _ in $(seq 50); do c rb-a ray status >/dev/null 2>&1 && c rb-b ray status >/dev/null 2>&1 && break; sleep 0.2; done
  fi
  local out room
  out="$(c rb-a ray create --open --name bench --hostname rb-a 2>&1)"
  room="$(echo "$out" | sed -n 's/.*ray join \([A-Za-z0-9]\{20,\}\).*/\1/p' | head -1)"
  [[ -n "$room" ]] || { echo "$out"; exit 1; }
  c rb-b ray join "$room" --name bench --hostname rb-b >/dev/null 2>&1
  for _ in $(seq 120); do
    if c rb-a ray status 2>/dev/null | grep -q 'rb-b'; then break; fi; sleep 1
  done
  B_VPN="$(c rb-b ray status --json | jq -r '.networks[0].my_ipv6')"
  for _ in $(seq 60); do c rb-a ping -c1 -W1 "$B_VPN" >/dev/null 2>&1 && break; sleep 1; done
  c rb-a ray firewall off >/dev/null; c rb-b ray firewall off >/dev/null
  echo "$B_VPN" > /tmp/rb-bvpn
  echo "B_VPN=$B_VPN"
}
case "$1" in up) shift; up "$@";; down) down;; esac
