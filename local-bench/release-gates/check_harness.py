from pathlib import Path
from tempfile import TemporaryDirectory
import sys,os
base=Path(__file__).resolve().parent
sys.path.insert(0,str(base))
with TemporaryDirectory() as d:
 p=Path(d);(p/'.servers').write_text('1 example.invalid test-a\n2 example.invalid test-b\n')
 os.environ['RAY_GATE_CLOUD_OUT']=d
 import cloud_bench as c
 b=c.bench;events=[]
 b.configure=lambda *args:['::1','::2']
 c.shape=lambda *args:None
 c.direct=lambda:None
 b.parallel=lambda fun:None
 obs={'samples':200}
 def idle(h,target):
  events.append(('idle',h,target));return obs
 b.idle_ping=idle
 def measure(src,dst,target,fl,profile,loss,num,wl,direction,measurement):
  assert events[-1]==('idle',src,target)
  assert measurement is obs
  events.append(('measure',src,profile));return {}
 b.measure=measure
 b.summarize=lambda *args:None
 c.cold=lambda *args:events.append(('cold',))
 sys.argv=['check','ray-test','binary','standalone','cold','uncapped','--runs','2']
 c.main()
 assert events==[('cold',),('idle','a','::2'),('measure','a','uncapped'),('idle','b','::1'),('measure','b','uncapped')],events
 print('Cloud harness records idle probes for both directions and skips them for cold transfers.')
