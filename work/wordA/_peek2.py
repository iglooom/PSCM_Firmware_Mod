import json
ents = json.load(open('encodings.json'))

def find(word, n=3):
    o = [(bin(e['mask']).count('1'), e) for e in ents if (word and e['mask']) and (word & e['mask']) == e['value']]
    o.sort(key=lambda x: -x[0])
    return o[:n]

for w in (0xA207, 0xA203, 0xA200, 0xE700, 0xE70A, 0xE708, 0x4C01, 0xF07C):
    print(f"== {w:04X}")
    for c, e in find(w):
        print("   bits%2d  %-10s %-28s %s" % (c, e['mnem'], e['operands'], e['bits']))
