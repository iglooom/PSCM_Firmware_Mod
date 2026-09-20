
"""Verify PSCM_LCA_enabler.md against the actual stock and patched VBFs."""
import struct, re, binascii
from pathlib import Path

def parse(p):
    raw=Path(p).read_bytes(); d=0; end=None
    for i,b in enumerate(raw):
        if b==0x7B: d+=1
        elif b==0x7D:
            d-=1
            if d==0: end=i+1; break
    blocks=[];pos=end
    while pos+8<=len(raw):
        a,l=struct.unpack_from(">II",raw,pos)
        if l==0 or pos+8+l+2>len(raw): break
        blocks.append((a,raw[pos+8:pos+8+l],struct.unpack_from(">H",raw,pos+8+l)[0]))
        pos+=8+l+2
    return raw,blocks,end

stock=dict((a,d) for a,d,_ in parse("CV6T-14C217-AR.VBF")[1])
patched=dict((a,d) for a,d,_ in parse("CV6T-14C217-AR_LCA_JOINT11_RAMP.VBF")[1])
s1,p1=stock[0x1C000],patched[0x1C000]

# The table exactly as printed in the document.
PATCHES=[(1,0x38F00,"F07C 0904 4C01 A203","E700 E700 E700 E700"),
         (2,0x397F6,"FF7C 2DC1 A209","E700 E700 E700"),
         (3,0x3B160,"A303","E700"),
         (4,0x39F5C,"AFE2","AFD8"),
         (5,0x3A05E,"B04E","B031")]
def rd(buf,off,n): return " ".join("%04X"%w for w in struct.unpack_from("<%dH"%n,buf,off))
ok=True
print("PATCH TABLE (document vs binaries)")
for n,off,st,pt in PATCHES:
    cnt=len(st.split())
    a,b=rd(s1,off,cnt),rd(p1,off,cnt)
    good = (a==st and b==pt)
    ok &= good
    print(f"  [{'OK' if good else 'FAIL'}] #{n} 0x{off:05X}  stock {a:<20} patched {b}")

print("\nP-ADDRESS -> OFFSET FORMULA  (P-0x0E000)*2")
for pw,off in ((0x2A780,0x38F00),(0x2ABFB,0x397F6),(0x2B8B0,0x3B160),
               (0x2AFAE,0x39F5C),(0x2B02F,0x3A05E),(0x0EEA4,0x01D48),(0x33800,0x4B000)):
    calc=(pw-0xE000)*2; good=calc==off; ok&=good
    print(f"  [{'OK' if good else 'FAIL'}] P:${pw:05X} -> 0x{calc:05X} (doc 0x{off:05X})")

print("\nCHECKSUM WORDS")
for lbl,off,val in (("word A",0x63FEA,0xD110),("word B",0x63FEC,0x3216)):
    got=struct.unpack_from("<H",s1,off)[0]; good=got==val; ok&=good
    print(f"  [{'OK' if good else 'FAIL'}] {lbl} 0x{off:05X} stock {got:04X} (doc {val:04X})")

print("\nDISPATCHER TABLES")
for lbl,t,exp in (("d1",0x2AFA4,"AFE7 AFB0 AFCF AFD4 AFD8 AFE2"),
                  ("d2",0x2B025,"B04F B031 B04E B04F B031 B04E")):
    got=" ".join("%04X"%struct.unpack_from("<H",s1,(t-0xE000)*2+4*i)[0] for i in range(6))
    good=got==exp; ok&=good
    print(f"  [{'OK' if good else 'FAIL'}] {lbl} {got}")
    # entry 5 must sit at table+10 words as the doc claims
    e5=(t+10-0xE000)*2
    good2 = e5 in (0x39F5C,0x3A05E); ok&=good2
    print(f"      [{'OK' if good2 else 'FAIL'}] entry5 offset 0x{e5:05X}")

print("\nCODE CONTEXT SNIPPETS")
for lbl,pw,n,exp in (("gate arm P:$2A77E",0x2A77E,11,"4C06 A207 F07C 0904 4C01 A203 E684 2DDE A902 E680 2DDE"),
                     ("entry P:$2ABF7",0x2ABF7,10,"F07C 2DDE 4C04 A20C FF7C 2DC1 A209 E700 4C83 A206"),
                     ("avail P:$2B8A8",0x2B8A8,10,"AC01 E181 F07C 2DB9 4C02 A306 E700 4C05 A303 E700"),
                     ("ramp arm P:$2AFD8",0x2AFD8,7,"8745 0400 F77C 03B0 F67C 2D50 2D46")):
    got=rd(s1,(pw-0xE000)*2,n); good=got==exp; ok&=good
    print(f"  [{'OK' if good else 'FAIL'}] {lbl}")
    if not good: print(f"        got {got}\n        doc {exp}")

print("\nTELEMETRY SITES")
got=rd(s1,0x01D48,2); good=got=="058F 0001"; ok&=good
print(f"  [{'OK' if good else 'FAIL'}] callback pointer stock {got} (doc 058F 0001)")
cave=set(struct.unpack_from("<60H",s1,0x4B000)); good=cave=={0xE70A}; ok&=good
print(f"  [{'OK' if good else 'FAIL'}] handler cave is E70A x60")

print("\nPATCHED IMAGE: only these 5 sites differ in code")
diff={i for i,(x,y) in enumerate(zip(s1,p1)) if x!=y}
allowed=set()
for _,off,st,_ in PATCHES: allowed|=set(range(off,off+len(st.split())*2))
allowed|=set(range(0x01D48,0x01D4C))|set(range(0x4B000,0x4B000+120))
allowed|={0x63FEA,0x63FEB,0x63FEC,0x63FED}
stray=diff-allowed; good=not stray; ok&=good
print(f"  [{'OK' if good else 'FAIL'}] {len(diff)} bytes differ, {len(stray)} outside patches+telemetry+checksums")

print("\n"+("ALL DOCUMENT CLAIMS VERIFIED" if ok else "*** DOCUMENT HAS ERRORS ***"))
