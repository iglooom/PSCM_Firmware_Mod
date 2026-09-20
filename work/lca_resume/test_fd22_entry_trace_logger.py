#!/usr/bin/env python3
import importlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

def test_format2_decoder_maps_x220f():
    m=importlib.import_module('fd22_entry_trace_logger')
    payload=bytes.fromhex('02 0004 0005 FFFE 0001 0000 0006 0003')
    d=m.decode_response(bytes.fromhex('62FD22')+payload)
    assert d['format']==2
    assert [d[n+'_signed'] for n in m.FIELD_NAMES]==[4,5,-2,1,0,6,3]
    try:m.decode_response(bytes.fromhex('62FD22')+bytes.fromhex('01')+payload[1:])
    except ValueError:pass
    else:raise AssertionError('format 1 accepted by format-2 logger')
