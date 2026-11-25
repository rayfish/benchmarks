import run as bench

try:
    bench.shape(100,20,0)
    for payload,rate,repeats in [(1200,'150M',[2,3]),(1000,'150M',[1,2]),(1200,'80M',[1,2])]:
        for repeat in repeats:
            order=['loss-tolerant','cubic'] if repeat % 2 == 0 else ['cubic','loss-tolerant']
            for controller in order:
                vpn=bench.set_controller(controller)
                bench.measure(bench.HOSTS[0],bench.HOSTS[1],vpn[1],controller,
                              f'UDP-{rate}-{payload}bytes',repeat,udp=True,udp_rate=rate,udp_payload=payload)
finally:
    bench.shape(0,0,0)
    bench.parallel(lambda h: bench.remote(h,'pkill -x iperf3 2>/dev/null || true'))
