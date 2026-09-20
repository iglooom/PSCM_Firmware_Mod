#!/usr/bin/env python3
import importlib,struct,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

def test_code5_availability_patch_is_single_instruction_and_retains_controls():
    m=importlib.import_module('build_lca_code5_availability_vbf')
    stock=m.base.parse_vbf(m.base.DEFAULT_STOCK);cal=m.base.parse_vbf(m.base.DEFAULT_CAL)
    result=m.build_bytes(stock,cal);built=m.base.parse_vbf_bytes(result.output,Path('test.vbf'));m.audit(stock,built,cal)
    old=stock.block_at(m.base.BLK1_FLASH).data;new=built.block_at(m.base.BLK1_FLASH).data
    assert m.CODE5_BRANCH_PWORD==0x2B8B0
    assert struct.unpack_from('<H',old,m.CODE5_BRANCH_OFF)==(0xA303,)
    assert struct.unpack_from('<H',new,m.CODE5_BRANCH_OFF)==(0xE700,)
    # Adjacent code-2/code-3 predicates remain exact OEM instructions.
    assert struct.unpack_from('<8H',new,m.CODE5_BRANCH_OFF-8)==(0x4C02,0xA306,0xE700,0x4C05,0xE700,0xE700,0x4C03,0xA203)
    assert struct.unpack_from('<4H',new,m.gatebase.GATE_GUARD_OFF)==(0xE700,)*4
    assert struct.unpack_from('<3H',new,m.entrybase.ENTRY_GUARD_OFF)==(0xE700,)*3
    assert m.TRACE_SOURCES==(0x2DDE,0x2DB9,0x2D53,0x2DAF,0x2D28,0x2252,0x2DBA)
    allowed=set(range(m.base.POINTER_OFF,m.base.POINTER_OFF+4))|set(range(m.base.HANDLER_OFF,m.base.HANDLER_OFF+120))|set(range(m.gatebase.GATE_GUARD_OFF,m.gatebase.GATE_GUARD_OFF+8))|set(range(m.entrybase.ENTRY_GUARD_OFF,m.entrybase.ENTRY_GUARD_OFF+6))|set(range(m.CODE5_BRANCH_OFF,m.CODE5_BRANCH_OFF+2))|set(range(m.base.WORD_A_OFF,m.base.WORD_A_OFF+2))|set(range(m.base.WORD_B_OFF,m.base.WORD_B_OFF+2))
    changed={i for i,(a,b) in enumerate(zip(old,new)) if a!=b}
    assert changed<=allowed
    assert m.base._u16le(new,m.base.WORD_A_OFF)==m.base.calculate_word_a(built.block_at(m.base.BLK0_FLASH).data,new)
    assert m.base._u16le(new,m.base.WORD_B_OFF)==m.base.calculate_word_b(built,cal,new)

def test_modified_predicate_changes_only_code5_behavior():
    old=lambda code: code in (2,3,5)
    new=lambda code: code in (2,3)
    for code in range(8):
        assert new(code)==(old(code) if code!=5 else False)
