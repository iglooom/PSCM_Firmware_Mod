#!/usr/bin/env python3
"""Decode the SBL service table + hunt UDS SIDs used as immediates."""
import struct, collections

P = "/home/gl/Projects/ford/PSCM/Research/bins/BV6T-14C220-AA/BV6T-14C220-AA_blk0_0x0009F000.bin"
d = open(P, 'rb').read()
WB = 0x4F800
NW = len(d)//2
w = list(struct.unpack('<%dH' % NW, d[:NW*2]))

NAME = {0x10:'DiagnosticSessionControl',0x11:'ECUReset',0x14:'ClearDiagInfo',
        0x19:'ReadDTCInfo',0x22:'ReadDataByIdentifier',0x23:'ReadMemoryByAddress',
        0x27:'SecurityAccess',0x28:'CommunicationControl',0x2E:'WriteDataByIdentifier',
        0x31:'RoutineControl',0x32:'StopRoutineByLocalId',0x33:'RequestRoutineResults',
        0x34:'RequestDownload',0x35:'RequestUpload',0x36:'TransferData',
        0x37:'RequestTransferExit',0x3D:'WriteMemoryByAddress',0x3E:'TesterPresent',
        0x85:'ControlDTCSetting',0x87:'LinkControl'}

print("=" * 78)
print("SERVICE TABLE @ file 0x0F56  (P:0x4FFAB)   -- byte pairs")
print("=" * 78)
t = d[0x0f56:]
for i in range(0, len(t)-1, 10):
    g = t[i:i+10]
    if len(g) < 10:
        print(f"  terminator/tail: {' '.join(f'{b:02x}' for b in g)}")
        break
    sid = g[0]
    print(f"  SID 0x{sid:02X} {NAME.get(sid,'?'):<26} raw={' '.join(f'{b:02x}' for b in g)}")
    print(f"        entries: " + '  '.join(f"({g[j]:02X},{g[j+1]:02X})" for j in range(0, 10, 2)))

print()
print("=" * 78)
print("32-bit / 16-bit IMMEDIATES that look like UDS SIDs or flash addresses")
print("=" * 78)
# common 2-word prefix e030 8654 -> look at what follows
pat = collections.Counter()
for i in range(NW-3):
    if w[i] == 0xe030 and w[i+1] == 0x8654:
        pat[(w[i+2], w[i+3])] += 1
print(" 'e030 8654' prefix followed by (w2,w3):")
for (a, b), c in sorted(pat.items()):
    print(f"    {a:04x} {b:04x}   x{c}   -> as u32le 0x{(b<<16)|a:08X}")

print()
print(" words equal to a UDS SID value:")
for i, x in enumerate(w):
    if x in NAME:
        ctx = ' '.join(f"{y:04x}" for y in w[max(0,i-3):i+4])
        print(f"    P:{WB+i:#07x} (b{2*i:#06x}) = {x:04x} {NAME[x]:<24} | {ctx}")

print()
print(" 32-bit LE immediates in the flash/RAM address range (0x00000..0x80000 word):")
seen = collections.Counter()
for i in range(NW-1):
    v = w[i] | (w[i+1] << 16)
    if 0x1000 <= v <= 0x80000 and (v & 0xff) == 0:
        seen[v] += 1
for v, c in sorted(seen.items()):
    if c >= 1:
        print(f"    0x{v:08X}  x{c}   (as byte addr 0x{v*2:08X})")
