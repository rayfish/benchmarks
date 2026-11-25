#!/usr/bin/env bash
# Prepares both hosts: packages, kernel WireGuard, headscale (TLS, private cert) +
# Tailscale, Rayfish release plus the two benchmark binaries. Needs .servers.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"; cd "$DIR"
: "${RAY_BENCH_BINARY:?Supply the Linux benchmark binary}"
: "${RAY_LTO_BINARY:?Supply the Linux LTO binary}"
: "${RAY_BASELINE_BINARY:?Supply the Linux baseline binary}"
A=$(awk '$3=="bench-a"{print $2}' .servers); B=$(awk '$3=="bench-b"{print $2}' .servers)
SCP=(scp -q -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$DIR/known_hosts" -o LogLevel=ERROR)
for s in a b; do for _ in $(seq 1 40); do echo true | ./r.sh $s 2>/dev/null && break; sleep 4; done; done
prep='set -e
cloud-init status --wait >/dev/null 2>&1 || true
export DEBIAN_FRONTEND=noninteractive
printf "iperf3 iperf3/start_daemon boolean false\n" | debconf-set-selections
apt-get update -qq
apt-get install -y -qq iperf3 wireguard-tools iproute2 iputils-ping python3 jq curl ethtool >/dev/null
systemctl stop unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1
curl -fsSL https://rayfish.xyz/install.sh | sh >/dev/null 2>&1
test -x /usr/local/bin/ray
uname -r; tailscale version | head -1; ray --version'
echo "$prep" | ./r.sh a > prep-a.log 2>&1 & echo "$prep" | ./r.sh b > prep-b.log 2>&1; wait
tail -n 3 prep-a.log; tail -n 3 prep-b.log
for h in $A $B; do
  "${SCP[@]}" "$RAY_BASELINE_BINARY" root@$h:/opt/ray-shipped
  "${SCP[@]}" "$RAY_BENCH_BINARY" root@$h:/opt/ray-bench
  "${SCP[@]}" "$RAY_LTO_BINARY" root@$h:/opt/ray-lto
done
for s in a b; do echo 'chmod 755 /opt/ray-*; /opt/ray-bench --version; /opt/ray-lto --version' | ./r.sh $s; done
# kernel WireGuard
ka=$(echo 'umask 077; wg genkey | tee /etc/wireguard/priv | wg pubkey' | ./r.sh a)
kb=$(echo 'umask 077; wg genkey | tee /etc/wireguard/priv | wg pubkey' | ./r.sh b)
echo "umask 077; printf '[Interface]\nPrivateKey = %s\nAddress = 192.0.2.1/24\nListenPort = 51820\n\n[Peer]\nPublicKey = $kb\nAllowedIPs = 192.0.2.2/32\nEndpoint = $B:51820\n' \"\$(cat /etc/wireguard/priv)\" > /etc/wireguard/wg0.conf" | ./r.sh a
echo "umask 077; printf '[Interface]\nPrivateKey = %s\nAddress = 192.0.2.2/24\nListenPort = 51820\n\n[Peer]\nPublicKey = $ka\nAllowedIPs = 192.0.2.1/32\nEndpoint = $A:51820\n' \"\$(cat /etc/wireguard/priv)\" > /etc/wireguard/wg0.conf" | ./r.sh b
# headscale with TLS on 443, reachable only from the two hosts
cat <<EOS | ./r.sh a
set -e
cd /tmp && curl -fsSLO https://github.com/juanfont/headscale/releases/download/v0.29.7/headscale_0.29.7_linux_amd64.deb
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ./headscale_0.29.7_linux_amd64.deb >/dev/null 2>&1
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -keyout /etc/headscale/key.pem -out /etc/headscale/cert.pem -days 7 -subj "/CN=bench-headscale" -addext "subjectAltName=IP:$A" -addext "basicConstraints=critical,CA:TRUE" 2>/dev/null
chown headscale:headscale /etc/headscale/key.pem /etc/headscale/cert.pem
sed -i -E 's|^server_url:.*|server_url: https://$A:443|; s|^listen_addr:.*|listen_addr: 0.0.0.0:443|; s|^metrics_listen_addr:.*|metrics_listen_addr: 127.0.0.1:9091|; s|^tls_cert_path:.*|tls_cert_path: /etc/headscale/cert.pem|; s|^tls_key_path:.*|tls_key_path: /etc/headscale/key.pem|' /etc/headscale/config.yaml
setcap cap_net_bind_service=+ep /usr/bin/headscale 2>/dev/null || true
iptables -A INPUT -p tcp --dport 443 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -s $A -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -s $B -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j DROP
cp /etc/headscale/cert.pem /usr/local/share/ca-certificates/bench-headscale.crt && update-ca-certificates >/dev/null 2>&1
systemctl enable headscale >/dev/null 2>&1; systemctl restart headscale; sleep 3; systemctl is-active headscale
headscale users create bench >/dev/null 2>&1
EOS
echo 'cat /etc/headscale/cert.pem' | ./r.sh a > hs-cert.pem
"${SCP[@]}" hs-cert.pem root@$B:/usr/local/share/ca-certificates/bench-headscale.crt
echo 'update-ca-certificates >/dev/null 2>&1' | ./r.sh b
key=$(echo 'uid=$(headscale users list -o json | jq -r ".[] | select(.name==\"bench\") | .id"); headscale preauthkeys create --user "$uid" --reusable --expiration 24h -o json 2>/dev/null | jq -r .key' | ./r.sh a)
for s in a b; do echo "systemctl restart tailscaled; sleep 2; tailscale up --login-server https://$A --authkey $key --hostname bench-$s --accept-dns=false --timeout 60s >/dev/null 2>&1; tailscale status --json | jq -c '{state: .BackendState}'; systemctl stop tailscaled" | ./r.sh $s; done
# Rayfish network
for s in a b; do echo 'ray up >/dev/null 2>&1; sleep 2; ray firewall off | tail -1' | ./r.sh $s; done
echo 'ray create --open --name benchmark --hostname bench-a >/dev/null 2>&1; ray status --json | jq -r ".networks[0].network_key"' | ./r.sh a > .room
echo "ray join $(cat .room) --name benchmark --hostname bench-b >/dev/null 2>&1; sleep 6; ray status --json | jq -c '.networks[0].peers[0].connection.conn_type'" | ./r.sh b
for s in a b; do echo 'systemctl stop rayfish; mkdir -p /etc/systemd/system/rayfish.service.d' | ./r.sh $s; done
echo SETUP DONE
