#!/usr/bin/env python3
import importlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

def test_format5_availability_producer_decoder():
    m=importlib.import_module('fd22_availability_producer_logger')
    payload=bytes.fromhex('05 0004 0005 FFFE 0006 0001 0001 0000')
    d=m.decode_response(bytes.fromhex('62FD22')+payload)
    assert d['format']==5
    assert [d[n+'_signed'] for n in m.FIELD_NAMES]==[4,5,-2,6,1,1,0]
    try:m.decode_response(bytes.fromhex('62FD22')+bytes([4])+payload[1:])
    except ValueError:pass
    else:raise AssertionError('format 4 accepted')
