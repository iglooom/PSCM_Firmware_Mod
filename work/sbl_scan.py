#!/usr/bin/env python3
"""Structural scan of the PSCM SBL (BV6T-14C220-AA) image.

DSP56800E is WORD addressed. VBF byte addr = 2 * word addr.
SBL loads at byte 0x0009F000 => word 0x4F800 (== the 'call' entry).
"""
import sys, struct, collections

P = "/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/BV6T-14C220-AA_blk0_0x0009F000.bin"
d = open(P, 'rb').read()
BYTE_BASE = 0x0009F000
WORD_BASE = BYTE_BASE // 2          # 0x4F800
NW = len(d) // 2
w = list(struct.unpack('<%dH' % NW, d[:NW * 2]))

print(f"len={len(d)} bytes = {NW} words ({NW:#x})")
print(f"word range P:{WORD_BASE:#07x} .. P:{WORD_BASE+NW:#07x}")
print()

# ---- histogram of word values, find repeated constants -------------------
cnt = collections.Counter(w)
print("top 15 repeated words:", [(f"{v:04x}", c) for v, c in cnt.most_common(15)])
print()

# ---- CRC nibble table check ---------------------------------------------
for i in range(NW - 16):
    if w[i:i+4] == [0x0000, 0x1021, 0x2042, 0x3063]:
        print(f"CRC16-CCITT(0x1021) NIBBLE table (non-reflected) at word "
              f"P:{WORD_BASE+i:#07x} (file byte {2*i:#06x})")
        print("   ", ' '.join(f"{x:04x}" for x in w[i:i+16]))
print()

# ---- hunt for UDS/KWP service-id tables ----------------------------------
UDS = {0x10:'DiagSessionControl',0x11:'ECUReset',0x14:'ClearDTC',0x18:'ReadDTCByStatus',
       0x19:'ReadDTCInfo',0x1A:'ReadEcuId',0x20:'StopDiagSession',0x21:'ReadDataByLocalId',
       0x22:'ReadDataByIdentifier',0x23:'ReadMemoryByAddress',0x27:'SecurityAccess',
       0x28:'CommunicationControl',0x2E:'WriteDataByIdentifier',0x2C:'DynDefineDataId',
       0x31:'RoutineControl / StartRoutineByLocalId',0x32:'StopRoutineByLocalId',
       0x33:'RequestRoutineResults',0x34:'RequestDownload',0x35:'RequestUpload',
       0x36:'TransferData',0x37:'RequestTransferExit',0x3D:'WriteMemoryByAddress',
       0x3E:'TesterPresent',0x3B:'WriteDataByLocalId',0x85:'ControlDTCSetting',
       0x87:'LinkControl',0xA1:'??'}

print("=== byte positions of plausible SID bytes, clustered ===")
hits = [i for i, b in enumerate(d) if b in UDS]
runs = []
cur = [hits[0]]
for x in hits[1:]:
    if x - cur[-1] <= 6:
        cur.append(x)
    else:
        runs.append(cur); cur = [x]
runs.append(cur)
for r in runs:
    if len(r) >= 4:
        lo, hi = r[0], r[-1]
        print(f"  file {lo:#06x}..{hi:#06x}  ({len(r)} sid-bytes)  "
              + ' '.join(f"{d[j]:02x}" for j in range(max(0,lo-4), min(len(d),hi+5))))
        print("      sids:", ','.join(hex(d[j]) for j in r))
print()

# ---- dump the tail table region -----------------------------------------
print("=== tail region 0x0f20..end as words ===")
for off in range(0x0f20, NW*2, 16):
    ws = w[off//2: off//2+8]
    print(f"  b{off:04x} w P:{WORD_BASE+off//2:#07x}  " + ' '.join(f"{x:04x}" for x in ws)
          + '   |' + ''.join(chr(c) if 32 <= c < 127 else '.' for c in d[off:off+16]) + '|')
