#!/usr/bin/env python3
"""Independent verifier for gate-open FD22 format-1 trace VBF."""
from __future__ import annotations
import hashlib
from pathlib import Path
import struct
import sys
import verify_fd22_snapshot_vbf_independent as ind

ROOT = Path(__file__).resolve().parents[2]
STOCK = ROOT / "CV6T-14C217-AR.VBF"
CAL = ROOT / "CV6T-14C218-AX.VBF"
OUT = ROOT / "CV6T-14C217-AR_LCA_GATE_FD22_TRACE.VBF"
HASHES = {
    STOCK.name: "cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5",
    CAL.name: "6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7",
    OUT.name: "f53d6f4c745b5552a74b5446709f3c732269cdfc0654c0584b430c43e1007ee0",
}
SOURCES = (0x2DDE, 0x2DB9, 0x2D53, 0x2DC1, 0x2DC3, 0x2DAF, 0x2DB8)
OEM_ARM = (0x4C06,0xA207,0xF07C,0x0904,0x4C01,0xA203,0xE684,0x2DDE,0xA902,0xE680,0x2DDE)
OPEN_ARM = (0x4C06,0xA207,0xE700,0xE700,0xE700,0xE700,0xE684,0x2DDE,0xA902,0xE680,0x2DDE)


def req(v,m):
    if not v: raise ind.VerifyError(m)


def verify_handler(vbf):
    w=ind.p_words(vbf,ind.CAVE_P,ind.CAVE_WORDS)
    req(w[:2]==(0xE081,0xD0B6),"format byte is not constant 1")
    sources=[]; offsets=[]; p=2
    while p<58:
        op,src,mv,shift,hs,ho,ls,lo=w[p:p+8]
        req((op,mv,shift,hs,ls)==(0xF07C,0x8110,0x5C28,0xD0E6,0xD1E6),f"bad field group at {p}")
        sources.append(src); offsets += [ho,lo]; p+=8
    req(tuple(sources)==SOURCES,"trace source list/order mismatch")
    req(offsets==list(range(1,15)),"scratch offsets are not 1..14")
    req(w[-2:]==(0xE58F,0xE708),"handler does not end Y0=15/RTS")
    return w


def verify():
    parsed={}
    for path in (STOCK,CAL,OUT):
        raw=path.read_bytes(); req(hashlib.sha256(raw).hexdigest()==HASHES[path.name],f"{path.name} hash drift")
        parsed[path.name]=ind.parse_vbf(path); ind.verify_container(parsed[path.name])
    stock,cal,out=parsed[STOCK.name],parsed[CAL.name],parsed[OUT.name]
    topo=[(0,0x9800),(0x1C000,0x64000),(0x4008C00,0x7400)]
    req([(b.load,len(b.payload)) for b in out.blocks]==topo,"output topology mismatch")
    req(ind.normalized_header(stock.raw[:stock.header_end])==ind.normalized_header(out.raw[:out.header_end]),"header changed outside checksum")
    req(stock.blocks[0].payload==out.blocks[0].payload and stock.blocks[2].payload==out.blocks[2].payload,"untouched block changed")
    req(ind.p_words(stock,0x2A77E,11)==OEM_ARM,"stock gate drift")
    req(ind.p_words(out,0x2A77E,11)==OPEN_ARM,"gate is not exact four-NOP edit")
    ind.prove_pointer(stock,out)
    words=verify_handler(out)
    diffs=ind.payload_differences(stock,out)
    allowed=set()
    for a,b in ((0x1DD48,0x1DD4C),(0x54F00,0x54F08),(0x67000,0x67078),(0x7FFEA,0x7FFEE)):
        allowed.update(range(a,b))
    req({a for a,_,_ in diffs}<=allowed,"unexpected payload difference")
    b0,b1=out.blocks[0],out.blocks[1]
    sa=struct.unpack_from('<H',b1.payload,ind.CRC_A_BLOCK1_END)[0]
    ca=ind.crc16_mcrf4xx(b0.payload+b1.payload[ind.CRC_A_BLOCK1_START:ind.CRC_A_BLOCK1_END])
    req(sa==ca==0x0960,"internal A mismatch")
    image=ind.make_linear_image(out,cal)
    sb=struct.unpack_from('<H',image,ind.CHECK_B_FLASH)[0]; cb=ind.sum_le16(image[:ind.CHECK_B_FLASH])
    req(sb==cb==0xFC89,"internal B mismatch")
    print("Independent gate-open FD22 format-1 trace verification")
    print(f"SHA-256: {HASHES[OUT.name]}")
    print("Container/block/file integrity: PASS")
    print(f"Internal A/B: {sa:04X}/{sb:04X} PASS")
    print("Gate: exact four NOPs; outer dispatch and OEM state stores retained")
    print("Handler: format=1; 60 words; 15 scratch stores; Y0=15; final RTS")
    print("Sources: "+','.join(f"X:${s:04X}" for s in SOURCES))
    print(f"Payload differences: {len(diffs)} bytes, all inside pointer/gate/handler/checksum spans")
    print("READY FOR CONTROLLED TEST")


def main():
    try: verify(); return 0
    except Exception as e:
        print(f"VERIFY FAILURE: {e}\nDO NOT FLASH",file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
