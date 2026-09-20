#!/usr/bin/env python3
"""Read-only, reproducible cruise/LCA dependency audit for PSCM firmware."""
from __future__ import annotations
import os, re, struct
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DBC = "/home/gl/Projects/ford/CANBus/CAN-HS.dbc"
XBASE = 0x4000
CFG = {
    "CV6T-AB": "bins/CV6T-14C386-AB/CV6T-14C386-AB_blk0_0x04008000.bin",
    "BV6T-AA": "bins/BV6T-14C386-AA/BV6T-14C386-AA_blk0_0x04008000.bin",
}
FW = {
    "CV6T-AH": [
        (0, "bins/CV6T-14C217-AH/CV6T-14C217-AH_blk0_0x00000000.bin"),
        (0xE000, "bins/CV6T-14C217-AH/CV6T-14C217-AH_blk1_0x0001C000.bin")],
    "BV6T-AF": [
        (0, "bins/BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin"),
        (0xE000, "bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin")],
    "CV6T-AR": [
        (0, "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin"),
        (0xE000, "bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin")],
}

def words(path):
    d = open(path, "rb").read()
    return list(struct.unpack("<%dH" % (len(d)//2), d))

def fw_load(spec):
    return [(base, words(os.path.join(ROOT, rel))) for base, rel in spec]

def W(spans, a):
    for base, ws in spans:
        if base <= a < base + len(ws): return ws[a-base]
    return None

def seq(spans, a, n): return [W(spans, a+i) for i in range(n)]
def hx(xs): return " ".join("----" if x is None else f"{x:04X}" for x in xs)

def iter_words(spans):
    for base, ws in spans:
        for i, x in enumerate(ws): yield base+i, x

def parse_dbc():
    msgs, cur = {}, None
    for line in open(DBC, errors="replace"):
        m = re.match(r"BO_\s+(\d+)\s+(\w+):", line)
        if m:
            cur = int(m.group(1)); msgs[cur] = {"name":m.group(2), "signals":[]}; continue
        m = re.match(r"\s*SG_\s+(\w+)\s*:\s*(\d+)\|(\d+)@([01])([+-])", line)
        if m and cur is not None:
            msgs[cur]["signals"].append((m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))))
    return msgs

def field(start, length):
    msb = start % 8; sh = msb-length+1
    return None if sh < 0 else (start//8, ((1<<length)-1)<<sh, sh)

def ptr_tables(ws):
    def ok(i):
        return i+3 < len(ws) and 0x41E0 <= ws[i] < 0x42B0 and ws[i+1]==0 and ws[i+2] in (5,7) and ws[i+3]==1
    out=[]; i=0
    while i<len(ws):
        if not ok(i): i+=1; continue
        e=[]; a=i
        while ok(i): e.append((ws[i],ws[i+2])); i+=4
        if len(e)>=4: out.append((XBASE+a,e))
    return out

def descriptor(ws, xa): return ws[xa-XBASE:xa-XBASE+10]
def extraction(ws):
    # The validated tables begin at X:$4000 and are phase-0, stride 5.
    out=[]
    for i in range(0, min(0x1E0,len(ws))-5, 5):
        mask, sh = ws[i+1]>>8, ws[i+1]&0xff; slot=ws[i+2]&0xff
        if mask and sh<=7 and slot and not slot&(slot-1):
            out.append((XBASE+i, ws[i:i+5]))
    return out

def abs_refs(spans, cell):
    reads=[]; writes=[]
    for base, ws in spans:
        for i in range(1,len(ws)):
            if ws[i] != cell: continue
            op=ws[i-1]; a=base+i-1
            if op in (0xF07C,0xF17C,0xF57C,0xF77C,0xFF7C): reads.append((a,op))
            if op in (0xD07C,0xD17C,0xD57C) or (op&0xFF80)==0xE680: writes.append((a,op))
    return reads,writes

def find_dispatch(spans):
    hits=[]
    for a,_ in iter_words(spans):
        s=seq(spans,a,28)
        # source load, enum 2->internal 1, enum 4->internal 2,
        # enum 6->internal 4 (optionally behind the CV6T config gate).
        if (s[0]==0xF07C and s[2:5]==[0x4C02,0xA203,0xE681]
                and s[7:10]==[0x4C04,0xA203,0xE682]
                and s[5]==s[10] and 0x4C06 in s[11:20]):
            hits.append((a,s[5],s))
    return hits

def find_enable(spans):
    out=[]
    for a,x in iter_words(spans):
        s=seq(spans,a,12)
        if x==0xF07C and s[2:11]==[0x4C02,0xA303,0xE700,0x4C05,0xA203,0xE700,0xE581,0xA901,0xE580]:
            out.append((a,s[1],s))
    return out

def branch_target(pc, op):
    off=op&0x7f
    if off>63: off-=128
    return pc+1+off

def main():
    dbc=parse_dbc()
    print("=== CAN reception and cruise-signal sensitivity ===")
    for label, rel in CFG.items():
        ws=words(os.path.join(ROOT,rel)); desc=[]
        for tab,ents in ptr_tables(ws):
            for xa,code in ents:
                r=descriptor(ws,xa); desc.append((r[0],xa,code,r))
        rx={cid for cid,xa,c,r in desc if c==7}
        print(f"{label}: RX IDs ({len(rx)}): " + " ".join(f"{x:03X}" for x in sorted(rx)))
        a5=[(xa,r) for cid,xa,c,r in desc if cid==0x0A5 and c==7]
        for xa,r in a5: print(f"  0x0A5 descriptor X:${xa:04X}: {hx(r)}")
        print(f"  0x1A0 descriptor: {'PRESENT' if 0x1A0 in rx else 'ABSENT'}")
        related=[]
        for cid in sorted(rx):
            for nm,st,ln,bo in dbc.get(cid,{}).get('signals',[]):
                if ((re.match(r"^(Cc|Acc)(?!el)",nm,re.I) or "Cruise" in nm)
                        and not nm.upper().startswith("CCP_")):
                    related.append((cid,nm,field(st,ln)))
        print("  named Cc/ACC signals in configured RX frames: " + (str(related) if related else "NONE"))
        # Exact CcStat geometry: 10|3@0+ => byte1, mask 0x0E, shift1 => 0x0E01.
        cand=[]
        slot_to_ids=defaultdict(list)
        for cid,xa,c,r in desc: slot_to_ids[r[8]&0xff].append(cid)
        exrecs=extraction(ws)
        print(f"  plausible stride-5 extraction records scanned: {len(exrecs)}")
        for xa,r in exrecs:
            if r[1]==0x0E01:
                cand.append((xa,r,slot_to_ids[r[2]&0xff]))
        print(f"  extraction records with exact CcStat spec 0E01: {len(cand)}")
        for xa,r,ids in cand: print(f"    X:${xa:04X}: {hx(r)} slot owners="+'+'.join(f'{i:03X}' for i in ids))
        # Positive sensitivity control: known LkaActvStats spec 7004 records.
        pos=[(xa,r) for xa,r in extraction(ws) if r[1]==0x7004]
        print(f"  positive control spec 7004 records: {len(pos)}")
        for xa,r in pos: print(f"    X:${xa:04X}: {hx(r)} getter pointer X:${xa+3:04X}")
    print("\n=== LCA state/code/torque structural trace ===")
    for label,spec in FW.items():
        sp=fw_load(spec); ds=find_dispatch(sp); en=find_enable(sp)
        print(f"{label}: dispatchers={len(ds)} torque-enables={len(en)}")
        if not ds or not en: continue
        da,lane,raw=ds[0]; ea,code,eraw=en[0]
        print(f"  dispatcher P:${da:05X}, lane X:${lane:04X}: {hx(raw[:18])}")
        print(f"  torque enable P:${ea:05X}, code X:${code:04X}: {hx(eraw)}")
        lr,lw=abs_refs(sp,lane); cr,cw=abs_refs(sp,code)
        lca_sites=[]
        for a,op in lr:
            s=seq(sp,a,5)
            if s[0] in (0xF07C,0xF17C) and s[1]==lane and s[2] in (0x4C04,0x4C84): lca_sites.append(a)
        print("  lane==4 sites: " + " ".join(f"P:${a:05X}" for a in lca_sites))
        print(f"  absolute refs lane read/write={len(lr)}/{len(lw)}; code read/write={len(cr)}/{len(cw)}")
        trans=[]
        for a,x in iter_words(sp):
            if x==0xE685 and W(sp,a+1)==code:
                # retain enough context to see preceding state-4 checks and branch targets
                trans.append(a)
        print("  MOVE #5 -> code sites: " + " ".join(f"P:${a:05X}" for a in trans))
        for a in trans:
            start=a-16; rr=seq(sp,start,20)
            print(f"    raw P:${start:05X}: {hx(rr)}")
            branches=[]
            for p in range(start,a):
                op=W(sp,p)
                if op is not None and (op&0xFF80) in (0xA200,0xA300): branches.append((p,op,branch_target(p,op)))
            print("    nearby A2/A3 targets: "+" ".join(f"P:${p:05X}/{op:04X}->P:${t:05X}" for p,op,t in branches))
        # Direct cruise coupling sensitivity: all immediate absolute refs to candidate RAM
        # cannot exist without a configured source; additionally report 1A0 bare words,
        # separating coincidence from an addressing opcode.
        bare=[]; real=[]
        for base,ws in sp:
            for i,x in enumerate(ws):
                if x==0x1A0:
                    bare.append(base+i)
                    if i and ws[i-1] in (0xF07C,0xF17C,0xF57C,0xF77C,0xFF7C,0xD07C,0xD17C,0xD57C): real.append(base+i-1)
        print(f"  literal 01A0 matches={len(bare)}; absolute X:$01A0 refs={len(real)}" + (" " + ' '.join(f'P:${a:05X}' for a in real) if real else ""))
        # Pointer-method positive control: find full-image MOVE.L #(&record[+3]),R2.
        for ptr in (0x408A,0x408F):
            sites=[]
            for a,x in iter_words(sp):
                if x==0xE40A and W(sp,a+1)==ptr: sites.append(a)
            print(f"  getter-pointer X:${ptr:04X} callers: " + (" ".join(f"P:${a:05X} [{hx(seq(sp,a,6))}]" for a in sites) or "NONE"))

    # Whole torque-function comparison: enable is at offset +0x38 from the
    # 107-word routine start in both builds (independently established earlier).
    ah=fw_load(FW["CV6T-AH"]); bv=fw_load(FW["BV6T-AF"])
    ahf=seq(ah,0x29586,107); bvf=seq(bv,0x27476,107)
    dif=[i for i,(x,y) in enumerate(zip(ahf,bvf)) if x!=y]
    print("\n=== CV6T-AH vs BV6T-AF torque function ===")
    print(f"107-word functions P:$29586/P:$27476: differing words={len(dif)} offsets=" + " ".join(f"+{i}" for i in dif))
    for i in dif:
        prev=(ahf[i-1],bvf[i-1]) if i else (None,None)
        print(f"  +{i:03d}: AH={ahf[i]:04X} BV={bvf[i]:04X} previous={prev[0]:04X}/{prev[1]:04X}")

if __name__=='__main__': main()
