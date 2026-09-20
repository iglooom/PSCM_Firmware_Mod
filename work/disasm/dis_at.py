import struct, sys, os
sys.path.insert(0, 'work/disasm')
sys.path.insert(0, 'work/wordA')
import dis56800e as base

SPEC = {
    'BV6T': [('bins/BV6T-14C217-AF/BV6T-14C217-AF_blk0_0x00000000.bin', 0),
             ('bins/BV6T-14C217-AF/BV6T-14C217-AF_blk1_0x0001C000.bin', 0xE000)],
    'CV6T': [('bins/CV6T-14C217-AR/CV6T-14C217-AR_blk0_0x00000000.bin', 0),
             ('bins/CV6T-14C217-AR/CV6T-14C217-AR_blk1_0x0001C000.bin', 0xE000)],
}


class Img:
    def __init__(self, spec):
        self.sp = []
        for p, b in spec:
            d = open(p, 'rb').read()
            self.sp.append((b, list(struct.unpack('<%dH' % (len(d) // 2), d))))

    def get(self, a):
        for b, w in self.sp:
            if b <= a < b + len(w):
                return w[a - b]
        return None

    def word(self, a):
        v = self.get(a)
        return 0xFFFF if v is None else v

    def window(self, a, n=4):
        return [self.word(a + k) for k in range(n)]


lbl = sys.argv[1]
start = int(sys.argv[2], 0)
n = int(sys.argv[3]) if len(sys.argv) > 3 else 40
img = Img(SPEC[lbl])
tbl = base.load_table()
a = start
for _ in range(n):
    txt, ln, _c = base.disasm(img.window(a, 4), 0, tbl)
    raw = ' '.join(f'{img.word(a+k):04X}' for k in range(ln))
    print(f'  P:${a:05X}  {raw:<14} {txt}')
    a += max(1, ln)
