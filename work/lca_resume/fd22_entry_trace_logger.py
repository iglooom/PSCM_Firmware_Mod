#!/usr/bin/env python3
"""Log FD22 format-2 phase-entry telemetry to CSV (read-only)."""
from __future__ import annotations
import argparse,csv,datetime as dt,importlib.util
from pathlib import Path
import time
from typing import Callable,TextIO

REQUEST='22FD22'; FORMAT=2
FIELD_NAMES=('lane_state','per_state_code','torque_accumulator','lca_consumer_inhibit','state_precondition','state_machine_phase','availability_value')
SOURCES=('X:$2DDE','X:$2DB9','X:$2D53','X:$2DC1','X:$2DC3','X:$2DAF','X:$220F')
CSV_FIELDS=['epoch_ns','utc','label','status','raw_response','format']
for name in FIELD_NAMES: CSV_FIELDS.extend((name,name+'_raw',name+'_signed'))

def decode_response(response:bytes|None)->dict[str,int]:
    if response is None:raise ValueError('timeout')
    if len(response)!=18 or response[:3]!=bytes.fromhex('62FD22'):raise ValueError('expected 18-byte 62 FD 22 response')
    payload=response[3:]
    if payload[0]!=FORMAT:raise ValueError(f'expected format {FORMAT}, got {payload[0]}')
    out={'format':FORMAT}
    for i,name in enumerate(FIELD_NAMES):
        v=int.from_bytes(payload[1+2*i:3+2*i],'big'); out[name]=out[name+'_raw']=v; out[name+'_signed']=v if v<0x8000 else v-0x10000
    return out

def utc(ns):return dt.datetime.fromtimestamp(ns/1e9,dt.timezone.utc).isoformat(timespec='microseconds')

def collect(request:Callable[[str,float],bytes|None],output:TextIO,rate:float,count:int|None,label:str,timeout=.5,progress=None):
    writer=csv.DictWriter(output,fieldnames=CSV_FIELDS,lineterminator='\n');writer.writeheader();output.flush();period=1/rate;n=0
    while count is None or n<count:
        start=time.monotonic();stamp=time.time_ns();row={'epoch_ns':stamp,'utc':utc(stamp),'label':label,'status':'','raw_response':''}
        response=request(REQUEST,timeout)
        if response is None:row['status']='timeout'
        else:
            row['raw_response']=response.hex()
            try:row.update(decode_response(response));row['status']='ok'
            except ValueError:row['status']='error'
        writer.writerow(row);output.flush();n+=1
        if progress:progress(row)
        delay=period-(time.monotonic()-start)
        if delay>0 and (count is None or n<count):time.sleep(delay)
    return n

def load_ecu(directory:Path):
    path=directory/'vbf.py';spec=importlib.util.spec_from_file_location('vbflasher_vbf',path)
    if spec is None or spec.loader is None:raise RuntimeError(f'cannot load {path}')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.Ecu

def selftest():
    payload=bytes.fromhex('02 0004 0005 FFFE 0001 0000 0006 0003');d=decode_response(bytes.fromhex('62FD22')+payload)
    assert [d[n+'_signed'] for n in FIELD_NAMES]==[4,5,-2,1,0,6,3]
    try:decode_response(bytes.fromhex('62FD22')+bytes([1])+payload[1:])
    except ValueError:pass
    else:raise AssertionError('format 1 accepted')
    print('SELFTEST PASS: format 2; '+', '.join(f'{n}={s}' for n,s in zip(FIELD_NAMES,SOURCES)));return 0

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--iface',default='can0');ap.add_argument('--rate',type=float,default=10);ap.add_argument('--timeout',type=float,default=.5);ap.add_argument('--count',type=int);ap.add_argument('--label',default='');ap.add_argument('--output','-o');ap.add_argument('--vbflasher-dir',type=Path,default=Path('/home/gl/Projects/ford/VBFlasher'));ap.add_argument('--selftest',action='store_true');args=ap.parse_args()
    if args.selftest:return selftest()
    if args.rate<=0 or args.timeout<=0 or (args.count is not None and args.count<=0):ap.error('rate, timeout and count must be positive')
    output=Path(args.output or ('fd22_entry_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')).resolve();Ecu=load_ecu(args.vbflasher_dir);ecu=Ecu(args.iface,0x730,0x738,execute=True)
    def request(payload,timeout):return ecu.req(payload,timeout=timeout,pending_timeout=timeout,what='FD22 entry trace')
    def progress(r):
        if r['status']=='ok':print(f"\r{r['utc']} state={r['lane_state']:>2} code={r['per_state_code']:>2} acc={r['torque_accumulator_signed']:>6} inhibit={r['lca_consumer_inhibit']:>2} pre={r['state_precondition']:>2} phase={r['state_machine_phase']:>2} avail={r['availability_value']:>2}",end='',flush=True)
        else:print(f"\r{r['utc']} {str(r['status']).upper()}",end='',flush=True)
    print(f'FD22 format-2 logger -> {output}\nPress Ctrl-C to stop safely.')
    total='interrupted'
    try:
        with output.open('w',newline='',encoding='utf-8') as handle:total=collect(request,handle,args.rate,args.count,args.label,args.timeout,progress)
    except KeyboardInterrupt:pass
    finally:
        sock=getattr(ecu,'s',None)
        if sock is not None:sock.close()
    print(f'\nStopped ({total}); CSV preserved at {output}');return 0
if __name__=='__main__':raise SystemExit(main())
