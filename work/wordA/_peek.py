import json
ents=json.load(open('encodings.json'))
def find(word,n=4):
    o=[(bin(e['mask']).count('1'),e) for e in ents if (word & e['mask'])==e['value']]
    o.sort(key=lambda x:-x[0]); return o[:n]
for w,nm in ((0xD5E0,'D5E0 store +disp'),(0x9D34,'9D34'),(0x9D20,'9D20'),
             (0x8748,'8748'),(0xF8E0,'F8E0'),(0xE40A,'E40A'),(0x4C06,'4C06'),
             (0xE684,'E684'),(0xE681,'E681'),(0xE682,'E682'),(0xE680,'E680'),
             (0xF07C,'F07C'),(0x4C02,'4C02'),(0x4C04,'4C04')):
    print("==",nm)
    for c,e in find(w):
        print("   bits%2d  %-10s %-28s %s"%(c,e['mnem'],e['operands'],e['bits']))
