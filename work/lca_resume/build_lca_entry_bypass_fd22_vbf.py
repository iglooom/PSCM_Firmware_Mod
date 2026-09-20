#!/usr/bin/env python3
"""Build gate-open AR plus phase-1 LCA-entry bypass and FD22 format-2 trace."""
from __future__ import annotations
import argparse, binascii, struct, sys
from pathlib import Path
import build_fd22_snapshot_vbf as base
import build_lca_gate_fd22_vbf as gatebase

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "CV6T-14C217-AR_LCA_ENTRY_FD22.VBF"
ENTRY_GUARD_PWORD = 0x2ABFB
ENTRY_GUARD_OFF = (ENTRY_GUARD_PWORD - base.BLK1_PWORD_BASE) * 2
OEM_ENTRY_GUARD = (0xFF7C, 0x2DC1, 0xA209)
TRACE_SOURCES = (0x2DDE, 0x2DB9, 0x2D53, 0x2DC1, 0x2DC3, 0x2DAF, 0x220F)


def make_handler():
    words=[0xE082,0xD0B6]  # format 2
    offset=1
    for src in TRACE_SOURCES:
        words += [0xF07C,src,0x8110,0x5C28,0xD0E6,offset,0xD1E6,offset+1]
        offset += 2
    words += [0xE58F,0xE708]
    if len(words)!=60: raise base.BuildError("handler length mismatch")
    return tuple(words)
TRACE_HANDLER=make_handler()


def build_bytes(stock, calibration):
    base.verify_oem_inputs(stock,calibration)
    block0=stock.block_at(base.BLK0_FLASH).data
    rec=stock.block_at(base.BLK1_FLASH); block1=bytearray(rec.data)
    base.expect_bytes(block1,base.POINTER_OFF,struct.pack('<2H',*base.OEM_POINTER_WORDS),'FD22 pointer')
    base.expect_bytes(block1,base.HANDLER_OFF,struct.pack('<60H',*([base.OEM_CAVE_WORD]*60)),'handler cave')
    base.expect_bytes(block1,gatebase.GATE_ARM_OFF,struct.pack('<11H',*gatebase.OEM_GATE_ARM),'dispatcher gate')
    base.expect_bytes(block1,ENTRY_GUARD_OFF,struct.pack('<3H',*OEM_ENTRY_GUARD),'phase-1 entry guard')
    base.expect_bytes(block1,base.WORD_A_OFF,struct.pack('<H',base.OEM_WORD_A),'word A')
    base.expect_bytes(block1,base.WORD_B_OFF,struct.pack('<H',base.OEM_WORD_B),'word B')
    block1[base.POINTER_OFF:base.POINTER_OFF+4]=struct.pack('<2H',*base.NEW_POINTER_WORDS)
    block1[base.HANDLER_OFF:base.HANDLER_OFF+120]=struct.pack('<60H',*TRACE_HANDLER)
    block1[gatebase.GATE_GUARD_OFF:gatebase.GATE_GUARD_OFF+8]=struct.pack('<4H',*([0xE700]*4))
    block1[ENTRY_GUARD_OFF:ENTRY_GUARD_OFF+6]=struct.pack('<3H',*([0xE700]*3))
    old_a=base._u16le(block1,base.WORD_A_OFF); new_a=base.calculate_word_a(block0,bytes(block1)); struct.pack_into('<H',block1,base.WORD_A_OFF,new_a)
    old_b=base._u16le(block1,base.WORD_B_OFF); new_b=base.calculate_word_b(stock,calibration,bytes(block1)); struct.pack_into('<H',block1,base.WORD_B_OFF,new_b)
    output=bytearray(stock.raw); output[rec.data_offset:rec.crc_offset]=block1
    crc=binascii.crc_hqx(block1,0xFFFF); struct.pack_into('>H',output,rec.crc_offset,crc)
    old_file=int(stock.raw[slice(*stock.checksum_span)],16); new_file=binascii.crc32(output[stock.data_start:])&0xffffffff
    width=stock.checksum_span[1]-stock.checksum_span[0]; output[slice(*stock.checksum_span)]=f'{new_file:0{width}X}'.encode()
    return base.BuildResult(bytes(output),old_a,new_a,old_b,new_b,rec.stored_crc,crc,old_file,new_file)


def audit(stock,built,calibration):
    base.verify_container(built)
    if built.data_start!=stock.data_start: raise base.BuildError('data_start changed')
    if [(b.address,len(b.data),b.data_offset,b.crc_offset) for b in built.blocks] != [(b.address,len(b.data),b.data_offset,b.crc_offset) for b in stock.blocks]: raise base.BuildError('block topology changed')
    old=stock.block_at(base.BLK1_FLASH).data; new=built.block_at(base.BLK1_FLASH).data
    base.expect_bytes(new,base.POINTER_OFF,struct.pack('<2H',*base.NEW_POINTER_WORDS),'built pointer')
    base.expect_bytes(new,base.HANDLER_OFF,struct.pack('<60H',*TRACE_HANDLER),'built handler')
    base.expect_bytes(new,gatebase.GATE_ARM_OFF,struct.pack('<11H',*gatebase.OPEN_GATE_ARM),'built dispatcher gate')
    base.expect_bytes(new,ENTRY_GUARD_OFF,struct.pack('<3H',*([0xE700]*3)),'built entry bypass')
    allowed=set(range(base.POINTER_OFF,base.POINTER_OFF+4))|set(range(base.HANDLER_OFF,base.HANDLER_OFF+120))|set(range(gatebase.GATE_GUARD_OFF,gatebase.GATE_GUARD_OFF+8))|set(range(ENTRY_GUARD_OFF,ENTRY_GUARD_OFF+6))|set(range(base.WORD_A_OFF,base.WORD_A_OFF+2))|set(range(base.WORD_B_OFF,base.WORD_B_OFF+2))
    changed={i for i,(a,b) in enumerate(zip(old,new)) if a!=b}
    if changed-allowed: raise base.BuildError(f'{len(changed-allowed)} unexpected payload changes')
    if base._u16le(new,base.WORD_A_OFF)!=base.calculate_word_a(built.block_at(base.BLK0_FLASH).data,new): raise base.BuildError('checksum A invalid')
    if base._u16le(new,base.WORD_B_OFF)!=base.calculate_word_b(built,calibration,new): raise base.BuildError('checksum B invalid')
    if any(ob.data!=nb.data or ob.stored_crc!=nb.stored_crc for ob,nb in zip(stock.blocks,built.blocks) if ob.address!=base.BLK1_FLASH): raise base.BuildError('untouched block changed')
    header_changes={i for i in range(stock.data_start) if stock.raw[i]!=built.raw[i]}
    if header_changes-set(range(*stock.checksum_span)): raise base.BuildError('header changed outside file checksum')
    return len(changed)


def selftest():
    try:
        stock,cal=base.parse_vbf(base.DEFAULT_STOCK),base.parse_vbf(base.DEFAULT_CAL)
        a=build_bytes(stock,cal); b=build_bytes(stock,cal)
        if a.output!=b.output: raise base.BuildError('nondeterministic output')
        audit(stock,base.parse_vbf_bytes(a.output,Path('entry-selftest.vbf')),cal)
        print('SELFTEST: ALL PASS'); return 0
    except Exception as e: print(f'SELFTEST FAILURE: {e}',file=sys.stderr); return 1


def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--selftest',action='store_true'); ap.add_argument('--output',type=Path,default=DEFAULT_OUTPUT); args=ap.parse_args()
    if args.selftest:return selftest()
    try:
        stock,cal=base.parse_vbf(base.DEFAULT_STOCK),base.parse_vbf(base.DEFAULT_CAL); r=build_bytes(stock,cal)
        audit(stock,base.parse_vbf_bytes(r.output,args.output),cal); base.write_atomic(args.output,r.output); rb=base.parse_vbf(args.output); audit(stock,rb,cal)
        if rb.raw!=r.output: raise base.BuildError('read-back mismatch')
        print(f'wrote: {args.output}\nsha256: {base.sha256(r.output)}')
        print('patches: dispatcher gate open; phase-1 X:$2DC1 test bypassed')
        print('FD22 format: 2; sources: '+','.join(f'X:${x:04X}' for x in TRACE_SOURCES))
        print(f'word A/B: {r.new_word_a:04X}/{r.new_word_b:04X}')
        print(f'block CRC/file checksum: {r.new_block_crc:04X}/{r.new_file_checksum:08X}')
        return 0
    except Exception as e: print(f'ERROR: {e}',file=sys.stderr); return 2
if __name__=='__main__': raise SystemExit(main())
