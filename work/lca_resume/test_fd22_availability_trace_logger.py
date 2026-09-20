#!/usr/bin/env python3
import importlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
def test_format3_availability_chain_decoder():
 m=importlib.import_module('fd22_availability_trace_logger');payload=bytes.fromhex('03 0004 0005 FFFE 0006 0003 0002 0001');d=m.decode_response(bytes.fromhex('62FD22')+payload)
 assert d['format']==3
 assert [d[n+'_signed'] for n in m.FIELD_NAMES]==[4,5,-2,6,3,2,1]
 try:m.decode_response(bytes.fromhex('62FD22')+bytes([2])+payload[1:])
 except ValueError:pass
 else:raise AssertionError('format 2 accepted')
