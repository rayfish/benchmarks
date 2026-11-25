#!/usr/bin/env bash
set -euo pipefail
cd "${RAY_GATE_VM_DIR:?Set RAY_GATE_VM_DIR to the directory containing the Ubuntu cloud image}"
bridge=rayg-br
case "${1:-}" in
    boot|resume)
        test ! -e a.pid && test ! -e b.pid
        test ! -e "/sys/class/net/$bridge"
        sudo -n ip link add "$bridge" type bridge
        sudo -n ip link set "$bridge" up
        test -f id_ed25519 || ssh-keygen -q -t ed25519 -N '' -f id_ed25519
        for node in a b; do
            if [[ "$node" == a ]]; then slot=2; cpus="${CPUS_A:-0,1}"; port="${VM_SSH_PORT_A:-2201}"; else slot=3; cpus="${CPUS_B:-2,3}"; port="${VM_SSH_PORT_B:-2202}"; fi
            tap="rayg-tap-$node"
            sudo -n ip tuntap add "$tap" mode tap user "$(id -un)"
            sudo -n ip link set "$tap" master "$bridge"
            sudo -n ip link set "$tap" up
            mgmt_mac="52:54:00:bf:61:0$slot"
            test_mac="52:54:00:bf:62:0$slot"
            if [[ "$1" == boot ]]; then
            cat >"user-$node" <<EOF
#cloud-config
users:
  - name: bench
    lock_passwd: true
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat id_ed25519.pub)
package_update: true
packages: [iperf3, ethtool, jq]
EOF
            cat >"meta-$node" <<EOF
instance-id: ray-gate-$node
local-hostname: gate-$node
EOF
            cat >"network-$node" <<EOF
version: 2
ethernets:
  mgmt:
    match: {macaddress: "$mgmt_mac"}
    set-name: mgmt0
    dhcp4: true
  test:
    match: {macaddress: "$test_mac"}
    set-name: link0
    addresses: ["198.51.100.$slot/24"]
    optional: true
EOF
            cloud-localds --network-config="network-$node" "seed-$node.img" "user-$node" "meta-$node"
            qemu-img create -f qcow2 -F qcow2 -b "$PWD/noble-server-cloudimg-amd64.img" "$node.qcow2" 12G
            fi
            sudo -n -u "$(id -un)" -g kvm taskset -c "$cpus" qemu-system-x86_64 \
                -enable-kvm -cpu host -machine q35 -smp 2 -m 4096 \
                -display none -serial "file:$node.serial" -monitor none \
                -daemonize -pidfile "$node.pid" \
                -drive "file=$node.qcow2,if=virtio" -drive "file=seed-$node.img,format=raw,if=virtio,readonly=on" \
                -netdev "user,id=mgmt,hostfwd=tcp:127.0.0.1:$port-:22" -device "virtio-net-pci,netdev=mgmt,mac=$mgmt_mac" \
                -netdev "tap,id=test,ifname=$tap,script=no,downscript=no,vhost=${GATE_VHOST:-off}" -device "virtio-net-pci,netdev=test,mac=$test_mac"
        done
        ;;
    ssh)
        [[ "$2" == a ]] && port="${VM_SSH_PORT_A:-2201}" || port="${VM_SSH_PORT_B:-2202}"
        shift 2
        exec ssh -q -o BatchMode=yes -o ConnectTimeout=3 -o StrictHostKeyChecking=accept-new \
            -o UserKnownHostsFile="$PWD/known_hosts" -i id_ed25519 -p "$port" bench@127.0.0.1 "$@"
        ;;
    down)
        for node in a b; do
            if [[ -f "$node.pid" ]]; then
                pid=$(cat "$node.pid")
                if [[ -r "/proc/$pid/comm" ]] && [[ "$(cat "/proc/$pid/comm")" == qemu-system-* ]]; then
                    kill -TERM "$pid"
                    for _ in {1..50}; do
                        kill -0 "$pid" 2>/dev/null || break
                        sleep 0.1
                    done
                fi
            fi
            if [[ -e "/sys/class/net/rayg-tap-$node" ]]; then
                sudo -n ip link del "rayg-tap-$node"
            fi
        done
        if [[ -e "/sys/class/net/$bridge" ]]; then sudo -n ip link del "$bridge"; fi
        ;;
esac
