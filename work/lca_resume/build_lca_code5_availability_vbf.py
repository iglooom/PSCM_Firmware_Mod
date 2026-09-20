#!/usr/bin/env python3
"""Build LCA code-5 availability fix with proven bypasses and format-5 telemetry."""
from __future__ import annotations
import argparse,binascii,struct,sys
from pathlib import Path
import build_fd22_snapshot_vbf as base
import build_lca_gate_fd22_vbf as gatebase
import build_lca_entry_bypass_fd22_vbf as entrybase
ROOT=Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT=ROOT/'CV6T-14C217-AR_LCA_CODE5_AVAIL_FD22.VBF'
CODE5_BRANCH_PWORD=0x2B8B0
CODE5_BRANCH_OFF=(CODE5_BRANCH_PWORD-base.BLK1_PWORD_BASE)*2
OEM_CODE5_BRANCH=0xA303
NEW_CODE5_BRANCH=0xE700
TRACE_SOURCES=(0x2DDE,0x2DB9,0x2D53,0x2DAF,0x2D28,0x2252,0x2DBA)
def make_handler():
 w=[0xE085,0xD0B6];off=1
 for src in TRACE_SOURCES:w += [0xF07C,src,0x8110,0x5C28,0xD0E6,off,0xD1E6,off+1];off+=2
 w += [0xE58F,0xE708]
 if len(w)!=60:raise base.BuildError('handler length mismatch')
 return tuple(w)
TRACE_HANDLER=make_handler()
def build_bytes(stock,cal):
 base.verify_oem_inputs(stock,cal);block0=stock.block_at(base.BLK0_FLASH).data;rec=stock.block_at(base.BLK1_FLASH);b=bytearray(rec.data)
 base.expect_bytes(b,base.POINTER_OFF,struct.pack('<2H',*base.OEM_POINTER_WORDS),'FD22 pointer');base.expect_bytes(b,base.HANDLER_OFF,struct.pack('<60H',*([base.OEM_CAVE_WORD]*60)),'handler cave');base.expect_bytes(b,gatebase.GATE_ARM_OFF,struct.pack('<11H',*gatebase.OEM_GATE_ARM),'dispatcher gate');base.expect_bytes(b,entrybase.ENTRY_GUARD_OFF,struct.pack('<3H',*entrybase.OEM_ENTRY_GUARD),'entry guard');base.expect_bytes(b,CODE5_BRANCH_OFF,struct.pack('<H',OEM_CODE5_BRANCH),'code-5 availability branch');base.expect_bytes(b,base.WORD_A_OFF,struct.pack('<H',base.OEM_WORD_A),'word A');base.expect_bytes(b,base.WORD_B_OFF,struct.pack('<H',base.OEM_WORD_B),'word B')
 b[base.POINTER_OFF:base.POINTER_OFF+4]=struct.pack('<2H',*base.NEW_POINTER_WORDS);b[base.HANDLER_OFF:base.HANDLER_OFF+120]=struct.pack('<60H',*TRACE_HANDLER);b[gatebase.GATE_GUARD_OFF:gatebase.GATE_GUARD_OFF+8]=struct.pack('<4H',*([0xE700]*4));b[entrybase.ENTRY_GUARD_OFF:entrybase.ENTRY_GUARD_OFF+6]=struct.pack('<3H',*([0xE700]*3));struct.pack_into('<H',b,CODE5_BRANCH_OFF,NEW_CODE5_BRANCH)
 olda=base._u16le(b,base.WORD_A_OFF);newa=base.calculate_word_a(block0,bytes(b));struct.pack_into('<H',b,base.WORD_A_OFF,newa);oldb=base._u16le(b,base.WORD_B_OFF);newb=base.calculate_word_b(stock,cal,bytes(b));struct.pack_into('<H',b,base.WORD_B_OFF,newb)
 out=bytearray(stock.raw);out[rec.data_offset:rec.crc_offset]=b;crc=binascii.crc_hqx(b,0xffff);struct.pack_into('>H',out,rec.crc_offset,crc);oldf=int(stock.raw[slice(*stock.checksum_span)],16);newf=binascii.crc32(out[stock.data_start:])&0xffffffff;width=stock.checksum_span[1]-stock.checksum_span[0];out[slice(*stock.checksum_span)]=f'{newf:0{width}X}'.encode()
 return base.BuildResult(bytes(out),olda,newa,oldb,newb,rec.stored_crc,crc,oldf,newf)
def audit(stock,built,cal):
 base.verify_container(built)
 if built.data_start!=stock.data_start:raise base.BuildError('data_start changed')
 if [(x.address,len(x.data),x.data_offset,x.crc_offset) for x in built.blocks]!=[(x.address,len(x.data),x.data_offset,x.crc_offset) for x in stock.blocks]:raise base.BuildError('block topology changed')
 old=stock.block_at(base.BLK1_FLASH).data;new=built.block_at(base.BLK1_FLASH).data
 base.expect_bytes(new,base.POINTER_OFF,struct.pack('<2H',*base.NEW_POINTER_WORDS),'built pointer');base.expect_bytes(new,base.HANDLER_OFF,struct.pack('<60H',*TRACE_HANDLER),'built handler');base.expect_bytes(new,gatebase.GATE_ARM_OFF,struct.pack('<11H',*gatebase.OPEN_GATE_ARM),'built dispatcher gate');base.expect_bytes(new,entrybase.ENTRY_GUARD_OFF,struct.pack('<3H',*([0xE700]*3)),'built entry bypass');base.expect_bytes(new,CODE5_BRANCH_OFF,struct.pack('<H',NEW_CODE5_BRANCH),'built code-5 availability patch');base.expect_bytes(new,CODE5_BRANCH_OFF-8,struct.pack('<8H',0x4C02,0xA306,0xE700,0x4C05,0xE700,0xE700,0x4C03,0xA203),'retained neighboring predicates')
 allowed=set(range(base.POINTER_OFF,base.POINTER_OFF+4))|set(range(base.HANDLER_OFF,base.HANDLER_OFF+120))|set(range(gatebase.GATE_GUARD_OFF,gatebase.GATE_GUARD_OFF+8))|set(range(entrybase.ENTRY_GUARD_OFF,entrybase.ENTRY_GUARD_OFF+6))|set(range(CODE5_BRANCH_OFF,CODE5_BRANCH_OFF+2))|set(range(base.WORD_A_OFF,base.WORD_A_OFF+2))|set(range(base.WORD_B_OFF,base.WORD_B_OFF+2));changed={i for i,(a,z) in enumerate(zip(old,new)) if a!=z}
 if changed-allowed:raise base.BuildError(f'{len(changed-allowed)} unexpected payload changes')
 if base._u16le(new,base.WORD_A_OFF)!=base.calculate_word_a(built.block_at(base.BLK0_FLASH).data,new):raise base.BuildError('checksum A invalid')
 if base._u16le(new,base.WORD_B_OFF)!=base.calculate_word_b(built,cal,new):raise base.BuildError('checksum B invalid')
 if any(a.data!=z.data or a.stored_crc!=z.stored_crc for a,z in zip(stock.blocks,built.blocks) if a.address!=base.BLK1_FLASH):raise base.BuildError('untouched block changed')
 hc={i for i in range(stock.data_start) if stock.raw[i]!=built.raw[i]}
 if hc-set(range(*stock.checksum_span)):raise base.BuildError('header changed outside file checksum')
 return len(changed)
def selftest():
 try:
  s,c=base.parse_vbf(base.DEFAULT_STOCK),base.parse_vbf(base.DEFAULT_CAL);a=build_bytes(s,c);z=build_bytes(s,c)
  if a.output!=z.output:raise base.BuildError('nondeterministic output')
  audit(s,base.parse_vbf_bytes(a.output,Path('code5-availability-selftest.vbf')),c);print('SELFTEST: ALL PASS');return 0
 except Exception as e:print(f'SELFTEST FAILURE: {e}',file=sys.stderr);return 1
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--selftest',action='store_true');ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT);args=ap.parse_args()
 if args.selftest:return selftest()
 try:
  s,c=base.parse_vbf(base.DEFAULT_STOCK),base.parse_vbf(base.DEFAULT_CAL);r=build_bytes(s,c);audit(s,base.parse_vbf_bytes(r.output,args.output),c);base.write_atomic(args.output,r.output);rb=base.parse_vbf(args.output);audit(s,rb,c)
  if rb.raw!=r.output:raise base.BuildError('read-back mismatch')
  print(f'wrote: {args.output}\nsha256: {base.sha256(r.output)}');print('control patches: dispatcher gate + phase-1 entry bypass (unchanged from joint4)');print('availability patch: P:$2B8B0 A303 -> E700 (code 5 only)');print('FD22 format 5: '+','.join(f'X:${x:04X}' for x in TRACE_SOURCES));print(f'word A/B: {r.new_word_a:04X}/{r.new_word_b:04X}');print(f'block CRC/file checksum: {r.new_block_crc:04X}/{r.new_file_checksum:08X}');return 0
 except Exception as e:print(f'ERROR: {e}',file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())
