#!/usr/bin/env python3
"""Log FD22 format-4 PSCM TX-source telemetry to CSV."""
from __future__ import annotations
import argparse,csv,datetime as dt,importlib.util,time
from pathlib import Path
REQUEST='22FD22';FORMAT=4
FIELD_NAMES=('lane_state','per_state_code','torque_accumulator','state_machine_phase','tx_availability_source','tx_payload_word1','lca_entry_value')
SOURCES=('X:$2DDE','X:$2DB9','X:$2D53','X:$2DAF','X:$7EBB','X:$3F5B','X:$220F')
CSV_FIELDS=['epoch_ns','utc','label','status','raw_response','format']
for n in FIELD_NAMES:CSV_FIELDS += [n,n+'_raw',n+'_signed']
def decode_response(response):
 if response is None:raise ValueError('timeout')
 if len(response)!=18 or response[:3]!=bytes.fromhex('62FD22'):raise ValueError('expected 18-byte 62 FD 22 response')
 p=response[3:]
 if p[0]!=FORMAT:raise ValueError(f'expected format {FORMAT}, got {p[0]}')
 d={'format':FORMAT}
 for i,n in enumerate(FIELD_NAMES):
  v=int.from_bytes(p[1+2*i:3+2*i],'big');d[n]=d[n+'_raw']=v;d[n+'_signed']=v if v<0x8000 else v-0x10000
 return d
def utc(ns):return dt.datetime.fromtimestamp(ns/1e9,dt.timezone.utc).isoformat(timespec='microseconds')
def collect(request,output,rate,count,label,timeout=.5,progress=None):
 w=csv.DictWriter(output,fieldnames=CSV_FIELDS,lineterminator='\n');w.writeheader();output.flush();period=1/rate;n=0
 while count is None or n<count:
  start=time.monotonic();stamp=time.time_ns();row={'epoch_ns':stamp,'utc':utc(stamp),'label':label,'status':'','raw_response':''};resp=request(REQUEST,timeout)
  if resp is None:row['status']='timeout'
  else:
   row['raw_response']=resp.hex()
   try:row.update(decode_response(resp));row['status']='ok'
   except ValueError:row['status']='error'
  w.writerow(row);output.flush();n+=1
  if progress:progress(row)
  delay=period-(time.monotonic()-start)
  if delay>0 and (count is None or n<count):time.sleep(delay)
 return n
def load_ecu(directory):
 path=directory/'vbf.py';spec=importlib.util.spec_from_file_location('vbflasher_vbf',path)
 if spec is None or spec.loader is None:raise RuntimeError(f'cannot load {path}')
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod.Ecu
def selftest():
 p=bytes.fromhex('04 0004 0005 FFFE 0006 0003 120C 0001');d=decode_response(bytes.fromhex('62FD22')+p);assert [d[n+'_signed'] for n in FIELD_NAMES]==[4,5,-2,6,3,0x120C,1]
 try:decode_response(bytes.fromhex('62FD22')+bytes([3])+p[1:])
 except ValueError:pass
 else:raise AssertionError('format 3 accepted')
 print('SELFTEST PASS: format 4; '+', '.join(f'{n}={s}' for n,s in zip(FIELD_NAMES,SOURCES)));return 0
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--iface',default='can0');ap.add_argument('--rate',type=float,default=10);ap.add_argument('--timeout',type=float,default=.5);ap.add_argument('--count',type=int);ap.add_argument('--label',default='');ap.add_argument('--output','-o');ap.add_argument('--vbflasher-dir',type=Path,default=Path('/home/gl/Projects/ford/VBFlasher'));ap.add_argument('--selftest',action='store_true');a=ap.parse_args()
 if a.selftest:return selftest()
 if a.rate<=0 or a.timeout<=0 or (a.count is not None and a.count<=0):ap.error('rate, timeout and count must be positive')
 out=Path(a.output or ('fd22_tx_source_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')).resolve();Ecu=load_ecu(a.vbflasher_dir);ecu=Ecu(a.iface,0x730,0x738,execute=True)
 def request(payload,timeout):return ecu.req(payload,timeout=timeout,pending_timeout=timeout,what='FD22 TX-source trace')
 def progress(r):
  if r['status']=='ok':print(f"\r{r['utc']} state={r['lane_state']:>2} code={r['per_state_code']:>2} acc={r['torque_accumulator_signed']:>6} phase={r['state_machine_phase']:>2} txsrc={r['tx_availability_source']:>2} txword={r['tx_payload_word1']:04X} entry={r['lca_entry_value']:>2}",end='',flush=True)
  else:print(f"\r{r['utc']} {str(r['status']).upper()}",end='',flush=True)
 print(f'FD22 format-4 logger -> {out}\nPress Ctrl-C to stop safely.');total='interrupted'
 try:
  with out.open('w',newline='',encoding='utf-8') as h:total=collect(request,h,a.rate,a.count,a.label,a.timeout,progress)
 except KeyboardInterrupt:pass
 finally:
  sock=getattr(ecu,'s',None)
  if sock is not None:sock.close()
 print(f'\nStopped ({total}); CSV preserved at {out}');return 0
if __name__=='__main__':raise SystemExit(main())
