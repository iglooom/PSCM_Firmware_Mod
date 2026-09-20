#!/usr/bin/env python3
"""Reassemble ISO-TP messages from a candump log and narrate the UDS exchange."""
import re, sys, collections

FRAME = re.compile(r'\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)')

def reassemble(path):
    """Yield (ts, canid, bytes) for each complete ISO-TP message."""
    pend = {}
    out = []
    for line in open(path):
        m = FRAME.match(line)
        if not m:
            continue
        ts, _if, cid, data = float(m.group(1)), m.group(2), int(m.group(3), 16), bytes.fromhex(m.group(4))
        if not data:
            continue
        pci = data[0] >> 4
        if pci == 0:                       # single frame
            n = data[0] & 0x0F
            out.append((ts, cid, data[1:1+n]))
        elif pci == 1:                     # first frame
            n = ((data[0] & 0x0F) << 8) | data[1]
            pend[cid] = [n, bytearray(data[2:]), ts]
        elif pci == 2:                     # consecutive
            st = pend.get(cid)
            if st is None:
                continue
            st[1] += data[1:]
            if len(st[1]) >= st[0]:
                out.append((st[2], cid, bytes(st[1][:st[0]])))
                del pend[cid]
        # pci == 3 -> flow control, ignore
    return out

NRC = {0x78: 'responsePending', 0x22: 'conditionsNotCorrect', 0x31: 'requestOutOfRange',
       0x33: 'securityAccessDenied', 0x35: 'invalidKey', 0x73: 'wrongBlockSequenceCounter'}

def narrate(msgs, txid=0x730):
    for ts, cid, d in msgs:
        tag = 'REQ' if cid == txid else 'RSP'
        sid = d[0]
        if sid == 0x36 or sid == 0x76:
            desc = f'{"TransferData" if sid==0x36 else "TransferData+"} blk={d[1]:02X} len={len(d)-2}'
        elif sid == 0x7F:
            desc = f'NEG svc={d[1]:02X} nrc={d[2]:02X} {NRC.get(d[2],"")}'
        else:
            desc = d.hex().upper()
        print(f'{ts:.6f} {cid:03X} {tag} {desc}')

if __name__ == '__main__':
    msgs = reassemble(sys.argv[1])
    if '--narrate' in sys.argv:
        narrate(msgs)
    else:
        # summary of services
        c = collections.Counter((cid, d[0]) for _, cid, d in msgs)
        for (cid, sid), n in sorted(c.items()):
            print(f'{cid:03X} SID {sid:02X}  x{n}')
