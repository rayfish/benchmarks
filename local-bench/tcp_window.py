import json,re,subprocess,time

def counter():
 text=subprocess.run(['ss','-tinH','sport = :5202'],capture_output=True,text=True,check=True).stdout
 values=[int(x) for x in re.findall(r'bytes_received:(\d+)',text)]
 return max(values,default=0)

start=time.monotonic()
while counter()<1024:
 if time.monotonic()-start>30:raise RuntimeError('Small TCP data connection did not start')
 time.sleep(.02)
origin=time.monotonic()
samples=[]
for offset in [4,14]:
 time.sleep(max(0,origin+offset-time.monotonic()))
 t=time.monotonic()
 samples.append({'elapsed_s':t-origin,'bytes_received':counter()})
a,b=samples
print(json.dumps({'samples':samples,'mbps':(b['bytes_received']-a['bytes_received'])*8/(b['elapsed_s']-a['elapsed_s'])/1e6}),flush=True)
