#!/usr/bin/env python3
"""Independent verifier for LCA TX-source trace VBF."""
import hashlib,struct,sys
from pathlib import Path
import verify_fd22_snapshot_vbf_independent as ind
ROOT=Path(__file__).resolve().parents[2];STOCK=ROOT/'CV6T-14C217-AR.VBF';CAL=ROOT/'CV6T-14C218-AX.VBF';OUT=ROOT/'CV6T-14C217-AR_LCA_TX_SOURCE_TRACE.VBF'
HASHES={STOCK.name:'cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5',CAL.name:'6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7',OUT.name:'d4071f3db007681433560d7bde0dd7354295c84f8b56ccc404dab47dc34a23b1'}
SOURCES=(0x2DDE,0x2DB9,0x2D53,0x2DAF,0x7EBB,0x3F5B,0x220F);OEM=(0x4C06,0xA207,0xF07C,0x0904,0x4C01,0xA203,0xE684,0x2DDE,0xA902,0xE680,0x2DDE);OPEN=(0x4C06,0xA207,0xE700,0xE700,0xE700,0xE700,0xE684,0x2DDE,0xA902,0xE680,0x2DDE)
def req(v,m):
 if not v:raise ind.VerifyError(m)
def verify_handler(v):
 w=ind.p_words(v,ind.CAVE_P,60);req(w[:2]==(0xE084,0xD0B6),'format mismatch');src=[];offs=[]
 for p in range(2,58,8):
  op,s,mv,sh,hs,ho,ls,lo=w[p:p+8];req((op,mv,sh,hs,ls)==(0xF07C,0x8110,0x5C28,0xD0E6,0xD1E6),f'bad group {p}');src.append(s);offs += [ho,lo]
 req(tuple(src)==SOURCES,'source order mismatch');req(offs==list(range(1,15)),'scratch offsets mismatch');req(w[-2:]==(0xE58F,0xE708),'return mismatch')
def verify():
 d={}
 for p in (STOCK,CAL,OUT):
  raw=p.read_bytes();req(hashlib.sha256(raw).hexdigest()==HASHES[p.name],f'{p.name} hash drift');d[p.name]=ind.parse_vbf(p);ind.verify_container(d[p.name])
 s,c,o=d[STOCK.name],d[CAL.name],d[OUT.name];req([(b.load,len(b.payload)) for b in o.blocks]==[(0,0x9800),(0x1C000,0x64000),(0x4008C00,0x7400)],'topology mismatch');req(ind.normalized_header(s.raw[:s.header_end])==ind.normalized_header(o.raw[:o.header_end]),'header drift');req(s.blocks[0].payload==o.blocks[0].payload and s.blocks[2].payload==o.blocks[2].payload,'untouched block drift')
 req(ind.p_words(s,0x2A77E,11)==OEM,'stock dispatcher drift');req(ind.p_words(o,0x2A77E,11)==OPEN,'dispatcher patch mismatch');req(ind.p_words(s,0x2ABFB,3)==(0xFF7C,0x2DC1,0xA209),'stock entry drift');req(ind.p_words(o,0x2ABFB,3)==(0xE700,)*3,'entry patch mismatch');req(ind.p_words(o,0x2ABF7,2)==(0xF07C,0x2DDE),'state test changed');req(ind.p_words(o,0x2ABFF,2)==(0x4C83,0xA206),'B==3 condition changed');req(ind.p_words(o,0x2AC01,4)==(0xE685,0x2DAF,0xE684,0x2DB9),'phase/code writes changed')
 ind.prove_pointer(s,o);verify_handler(o);diff=ind.payload_differences(s,o);allowed=set()
 for a,b in ((0x1DD48,0x1DD4C),(0x54F00,0x54F08),(0x557F6,0x557FC),(0x67000,0x67078),(0x7FFEA,0x7FFEE)):allowed.update(range(a,b))
 req({a for a,_,_ in diff}<=allowed,'unexpected payload difference');b0,b1=o.blocks[0],o.blocks[1];sa=struct.unpack_from('<H',b1.payload,ind.CRC_A_BLOCK1_END)[0];ca=ind.crc16_mcrf4xx(b0.payload+b1.payload[ind.CRC_A_BLOCK1_START:ind.CRC_A_BLOCK1_END]);req(sa==ca==0x15A9,'A mismatch');image=ind.make_linear_image(o,c);sb=struct.unpack_from('<H',image,ind.CHECK_B_FLASH)[0];cb=ind.sum_le16(image[:ind.CHECK_B_FLASH]);req(sb==cb==0x4578,'B mismatch')
 print('Independent LCA TX-source-trace verification');print('SHA-256:',HASHES[OUT.name]);print('Container/block/file integrity: PASS');print(f'Internal A/B: {sa:04X}/{sb:04X} PASS');print('Control patches: byte-identical dispatcher + phase-entry bypasses from joint4');print('State test, B==3 condition, phase/code writes: RETAINED');print('FD22: format 4; sources '+','.join(f'X:${x:04X}' for x in SOURCES));print(f'Payload differences: {len(diff)} bytes, all classified');print('READY FOR CONTROLLED TEST')
def main():
 try:verify();return 0
 except Exception as e:print(f'VERIFY FAILURE: {e}\nDO NOT FLASH',file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
