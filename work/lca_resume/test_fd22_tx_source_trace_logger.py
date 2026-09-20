#!/usr/bin/env python3
import importlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

def test_format4_tx_source_decoder():
    m=importlib.import_module('fd22_tx_source_trace_logger')
    payload=bytes.fromhex('04 0004 0005 FFFE 0006 0003 120C 0001')
    d=m.decode_response(bytes.fromhex('62FD22')+payload)
    assert d['format']==4
    assert [d[n+'_signed'] for n in m.FIELD_NAMES]==[4,5,-2,6,3,0x120C,1]
    try:m.decode_response(bytes.fromhex('62FD22')+bytes([3])+payload[1:])
    except ValueError:pass
    else:raise AssertionError('format 3 accepted')
