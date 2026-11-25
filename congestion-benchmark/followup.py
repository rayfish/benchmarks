import run as bench

try:
    for name, rate, delay in [('clean',0,0),('100M-40ms-0loss',100,20)]:
        bench.shape(rate,delay,0)
        for repeat in [2,3]:
            order=['loss-tolerant','cubic'] if repeat == 2 else ['cubic','loss-tolerant']
            for controller in order:
                vpn=bench.set_controller(controller)
                bench.measure(bench.HOSTS[0],bench.HOSTS[1],vpn[1],controller,name,repeat,streams=4)
finally:
    bench.shape(0,0,0)
    bench.parallel(lambda h: bench.remote(h,'pkill -x iperf3 2>/dev/null || true'))
