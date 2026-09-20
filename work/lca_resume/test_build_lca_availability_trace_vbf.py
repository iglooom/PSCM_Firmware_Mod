#!/usr/bin/env python3
import importlib,struct,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

def test_availability_trace_retains_only_proven_bypasses():
    m=importlib.import_module('build_lca_availability_trace_vbf')
    stock=m.base.parse_vbf(m.base.DEFAULT_STOCK);cal=m.base.parse_vbf(m.base.DEFAULT_CAL)
    result=m.build_bytes(stock,cal);built=m.base.parse_vbf_bytes(result.output,Path('test.vbf'));m.audit(stock,built,cal)
    old=stock.block_at(m.base.BLK1_FLASH).data;new=built.block_at(m.base.BLK1_FLASH).data
    assert struct.unpack_from('<4H',new,m.gatebase.GATE_GUARD_OFF)==(0xE700,)*4
    assert struct.unpack_from('<3H',old,m.entrybase.ENTRY_GUARD_OFF)==(0xFF7C,0x2DC1,0xA209)
    assert struct.unpack_from('<3H',new,m.entrybase.ENTRY_GUARD_OFF)==(0xE700,)*3
    assert m.TRACE_HANDLER[:2]==(0xE083,0xD0B6)
    assert m.TRACE_SOURCES==(0x2DDE,0x2DB9,0x2D53,0x2DAF,0x2213,0x226C,0x220F)
    assert m.TRACE_HANDLER[-2:]==(0xE58F,0xE708)
    allowed=set(range(m.base.POINTER_OFF,m.base.POINTER_OFF+4))|set(range(m.base.HANDLER_OFF,m.base.HANDLER_OFF+120))|set(range(m.gatebase.GATE_GUARD_OFF,m.gatebase.GATE_GUARD_OFF+8))|set(range(m.entrybase.ENTRY_GUARD_OFF,m.entrybase.ENTRY_GUARD_OFF+6))|set(range(m.base.WORD_A_OFF,m.base.WORD_A_OFF+2))|set(range(m.base.WORD_B_OFF,m.base.WORD_B_OFF+2))
    changed={i for i,(a,b) in enumerate(zip(old,new)) if a!=b}
    assert changed<=allowed
    assert m.base._u16le(new,m.base.WORD_A_OFF)==m.base.calculate_word_a(built.block_at(m.base.BLK0_FLASH).data,new)
    assert m.base._u16le(new,m.base.WORD_B_OFF)==m.base.calculate_word_b(built,cal,new)
