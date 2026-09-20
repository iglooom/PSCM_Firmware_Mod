#!/usr/bin/env python3
from pathlib import Path
import re, csv, json, struct, sys

VBF = Path(sys.argv[1] if len(sys.argv)>1 else '/mnt/data/CV6T-14C217-AR.VBF')
OUT = Path(sys.argv[2] if len(sys.argv)>2 else '/mnt/data/pscm_disasm')
OUT.mkdir(parents=True, exist_ok=True)

# MC56F8366 peripheral base map from datasheet Table 4-9.
MODULES = [
 (0xF020,0xF03F,'EMI'), (0xF040,0xF07F,'TMRA'), (0xF080,0xF0BF,'TMRB'),
 (0xF0C0,0xF0FF,'TMRC'), (0xF100,0xF13F,'TMRD'), (0xF140,0xF15F,'PWMA'),
 (0xF160,0xF17F,'PWMB'), (0xF180,0xF18F,'DEC0'), (0xF190,0xF19F,'DEC1'),
 (0xF1A0,0xF1FF,'ITCN'), (0xF200,0xF23F,'ADCA'), (0xF240,0xF26F,'ADCB'),
 (0xF270,0xF27F,'TSENSOR'), (0xF280,0xF28F,'SCI0'), (0xF290,0xF29F,'SCI1'),
 (0xF2A0,0xF2AF,'SPI0'), (0xF2B0,0xF2BF,'SPI1'), (0xF2C0,0xF2CF,'COP'),
 (0xF2D0,0xF2DF,'CLKGEN'), (0xF2E0,0xF2FF,'GPIOA'), (0xF300,0xF30F,'GPIOB'),
 (0xF310,0xF31F,'GPIOC'), (0xF320,0xF32F,'GPIOD'), (0xF330,0xF33F,'GPIOE'),
 (0xF340,0xF34F,'GPIOF'), (0xF350,0xF35F,'GPIOG'), (0xF360,0xF37F,'SIM'),
 (0xF400,0xF7FF,'FLASH/BOOT'), (0xF800,0xF8FF,'FLEXCAN'), (0xFA00,0xFAFF,'FLEXCAN2'),
]

# Exact registers useful for this firmware. Extend freely.
REG = {
 0xF145:'PWMA_PWMCM',
 0xF205:'ADCA_SDIS', 0xF209:'ADCA_RSLT0',
 0xF24A:'ADCB_RSLT1', 0xF24D:'ADCB_RSLT4', 0xF24E:'ADCB_RSLT5',
 0xF054:'TMRA1_HOLD', 0xF064:'TMRA2_HOLD', 0xF065:'TMRA2_CNTR', 0xF075:'TMRA3_CNTR',
 0xF2E7:'GPIOA_IPR',
}
# FlexCAN MB0..15 layout, base $F840, stride 8 words.
for mb in range(16):
    b=0xF840+mb*8
    REG[b+0]=f'FCMB{mb}_CONTROL'; REG[b+1]=f'FCMB{mb}_ID_HIGH'; REG[b+2]=f'FCMB{mb}_ID_LOW'
    for j in range(4): REG[b+3+j]=f'FCMB{mb}_DATA{j}'
# Major FlexCAN controller regs (datasheet table 4-38)
REG.update({0xF800:'FCMCR',0xF803:'FCCTL0',0xF804:'FCCTL1',0xF805:'FCTMR',0xF806:'FCMAXMB',
            0xF807:'FCIMASK2',0xF808:'FCRXGMASK_H',0xF809:'FCRXGMASK_L',
            0xF80A:'FCRX14MASK_H',0xF80B:'FCRX14MASK_L',0xF80C:'FCRX15MASK_H',0xF80D:'FCRX15MASK_L'})

VECTOR_NAMES = {
 0:'RESET',1:'COP_RESET',2:'ILLEGAL_INSTRUCTION',3:'SWI3',4:'HW_STACK_OVERFLOW',5:'MISALIGNED_LONG',
 6:'SWI2',7:'SWI1',16:'SWI0',17:'IRQA',20:'LOW_VOLTAGE',
 56:'TMRC0',57:'TMRC1',58:'TMRC2',59:'TMRC3',60:'TMRB0',61:'TMRB1',62:'TMRB2',63:'TMRB3',
 64:'TMRA0',65:'TMRA1',66:'TMRA2',67:'TMRA3',68:'SCI0_TX_EMPTY',69:'SCI0_TX_IDLE',71:'SCI0_RX_ERROR',72:'SCI0_RX_FULL',
 73:'ADCB_EOS',74:'ADCA_EOS',75:'ADCB_LIMIT',76:'ADCA_LIMIT',77:'PWMB_RELOAD',78:'PWMA_RELOAD',79:'PWMB_FAULT',80:'PWMA_FAULT',
 81:'SWI_LP',82:'FLEXCAN2_BUSOFF',83:'FLEXCAN2_ERROR',84:'FLEXCAN2_WAKEUP',85:'FLEXCAN2_MB',
}

def module(addr):
    for lo,hi,n in MODULES:
        if lo <= addr <= hi: return n
    return None

def regname(addr):
    if addr in REG: return REG[addr]
    m=module(addr)
    return f'{m}+0x{addr-next(lo for lo,hi,n in MODULES if n==m and lo<=addr<=hi):X}' if m else None

def parse_vbf(blob):
    # textual header ends at closing brace followed by NULs; first block header then begins.
    m=re.search(br'file_checksum\s*=\s*0x[0-9A-Fa-f]+;\s*\}', blob)
    if not m: raise RuntimeError('VBF header end not found')
    pos=m.end()
    while pos < len(blob) and blob[pos]==0: pos+=1
    # Header may end before CR/LF/NUL combination; robustly scan for first expected block header.
    expected=[(0x00000000,0x00009800),(0x0001C000,0x00064000),(0x04008C00,0x00007400)]
    blocks=[]
    search=m.end()
    for a,l in expected:
        sig=struct.pack('>II',a,l)
        idx=blob.find(sig, search)
        if idx<0: raise RuntimeError(f'block {a:08X}/{l:X} not found')
        payload=blob[idx+8:idx+8+l]
        csum=blob[idx+8+l:idx+8+l+2]
        blocks.append((a,l,payload,csum,idx))
        search=idx+8+l+2
    return blob[:blocks[0][4]], blocks

def words_le(payload):
    if len(payload)%2: raise ValueError('odd payload')
    return list(struct.unpack('<%dH'%(len(payload)//2),payload))

blob=VBF.read_bytes(); header,blocks=parse_vbf(blob)
(OUT/'header.txt').write_text(header.decode('latin1','replace'))

# Build sparse P and X maps. VBF addresses are byte pointers; convert to word addresses.
pmap={}; xmap={}
for a,l,payload,csum,off in blocks:
    ws=words_le(payload)
    if a < 0x04000000:
        base=a//2
        for i,w in enumerate(ws): pmap[base+i]=w
    else:
        # Combined-image X-space convention used by this VBF: low 24 bits are byte pointer.
        base=(a & 0x00FFFFFF)//2
        for i,w in enumerate(ws): xmap[base+i]=w

# Raw images, preserving gaps as 0xFFFF (separate occupancy JSON tells what is really present).
maxp=max(pmap); pimg=bytearray()
for a in range(maxp+1): pimg += struct.pack('<H', pmap.get(a,0xFFFF))
(OUT/'P_space_le.bin').write_bytes(pimg)
minx,maxx=min(xmap),max(xmap); ximg=bytearray()
for a in range(minx,maxx+1): ximg += struct.pack('<H',xmap[a])
(OUT/f'X_flash_{minx:04X}_{maxx:04X}_le.bin').write_bytes(ximg)

# Discover validated JSR ABS19 form used throughout this image: E254..E257 + low16.
def jsr_target(w0,w1):
    if 0xE254 <= w0 <= 0xE257: return ((w0-0xE254)<<16)|w1
    return None

# First-pass labels: vectors + JSR destinations.
labels={}
for v in range(86):
    a=v*2; labels[a]=f'vector_{v:02d}_{VECTOR_NAMES.get(v,"RESERVED")}';
    if a in pmap and a+1 in pmap:
        t=jsr_target(pmap[a],pmap[a+1])
        if t is not None and t in pmap:
            labels.setdefault(t, f'isr_{VECTOR_NAMES.get(v, f"vector_{v}").lower()}')
for a,w in pmap.items():
    if a+1 in pmap:
        t=jsr_target(w,pmap[a+1])
        if t is not None and t in pmap: labels.setdefault(t,f'sub_{t:05X}')

# Peripheral xrefs from validated encodings.
xrefs=[]
for a,w in sorted(pmap.items()):
    if a+1 not in pmap: continue
    w1=pmap[a+1]
    if w==0xF07C and module(w1):
        xrefs.append((a,'read_abs_to_A',w1,regname(w1)))
    if w==0x8748 and module(w1):
        xrefs.append((a,'load_addr_R0',w1,regname(w1)))

with (OUT/'peripheral_xrefs.csv').open('w',newline='') as f:
    cw=csv.writer(f); cw.writerow(['p_address','kind','x_address','register'])
    for a,k,x,n in xrefs: cw.writerow([f'{a:05X}',k,f'{x:04X}',n])

# Vector table report.
with (OUT/'vectors.txt').open('w') as f:
    for v in range(86):
        a=v*2; w0=pmap.get(a); w1=pmap.get(a+1); t=jsr_target(w0,w1) if w0 is not None and w1 is not None else None
        f.write(f'{v:02d} P:${a:05X} {VECTOR_NAMES.get(v,"RESERVED"):<22} ')
        if t is not None: f.write(f'JSR P:${t:05X}  {labels.get(t,"")}')
        else: f.write(f'.word {w0:04X} {w1:04X}' if w0 is not None else 'missing')
        f.write('\n')

# Conservative decoder: only encodings validated from Freescale/CodeWarrior examples or self-evident vector form.
def decode_at(a):
    w=pmap[a]
    if w==0xE708: return 1,'rts',''
    if a+1 in pmap:
        w1=pmap[a+1]
        t=jsr_target(w,w1)
        if t is not None:
            return 2,'jsr',f'P:${t:05X}' + (f' <{labels[t]}>' if t in labels else '')
        if w==0xF07C:
            n=regname(w1); c=f' ; {n}' if n else ''
            return 2,'move.w',f'X:${w1:04X},A{c}'
        if w==0x8748:
            n=regname(w1); c=f' ; {n}' if n else ''
            return 2,'moveu.w',f'#${w1:04X},R0{c}'
    return 1,'.word',f'0x{w:04X}'

# Full annotated sparse listing.
with (OUT/'CV6T-14C217-AR.conservative.asm').open('w') as f:
    f.write('; CV6T-14C217-AR.VBF — conservative MC56F8366/56800E listing\n')
    f.write('; Only validated encodings are decoded. Unknown words deliberately remain .word.\n')
    f.write('; P addresses are 16-bit word addresses.\n\n')
    addrs=sorted(pmap); i=0
    while i<len(addrs):
        a=addrs[i]
        # start new ORG on discontinuity
        if i==0 or a!=addrs[i-1]+1: f.write(f'\n        org     P:${a:05X}\n')
        if a in labels: f.write(f'\n{labels[a]}:\n')
        n,mn,ops=decode_at(a)
        raw=' '.join(f'{pmap[a+j]:04X}' for j in range(n) if a+j in pmap)
        f.write(f'P:{a:05X}  {raw:<10}  {mn:<8} {ops}\n')
        # advance by n, but only if contiguous words exist
        i += n

# Focused ranges that are immediately useful.
focus=[(0x00000,0x00100,'vectors_startup'),(0x04080,0x04200,'pwma_reload_area'),(0x19C00,0x1AB00,'flexcan_mb12_13_area')]
for lo,hi,name in focus:
    with (OUT/f'{name}.asm').open('w') as f:
        a=lo
        while a<=hi:
            if a not in pmap: a+=1; continue
            if a in labels: f.write(f'\n{labels[a]}:\n')
            n,mn,ops=decode_at(a); raw=' '.join(f'{pmap[a+j]:04X}' for j in range(n) if a+j in pmap)
            f.write(f'P:{a:05X}  {raw:<10}  {mn:<8} {ops}\n'); a+=n

# Function/call target list.
with (OUT/'functions.csv').open('w',newline='') as f:
    cw=csv.writer(f); cw.writerow(['address','label'])
    for a,n in sorted(labels.items()):
        if n.startswith(('sub_','isr_')): cw.writerow([f'{a:05X}',n])

meta={'source':VBF.name,'p_ranges':[], 'x_ranges':[], 'function_labels':sum(n.startswith(('sub_','isr_')) for n in labels.values()), 'peripheral_xrefs':len(xrefs)}
for a,l,_,csum,_ in blocks:
    d={'vbf_byte_address':f'0x{a:08X}','length_bytes':l,'block_checksum_hex':csum.hex()}
    (meta['p_ranges'] if a<0x04000000 else meta['x_ranges']).append(d)
(OUT/'metadata.json').write_text(json.dumps(meta,indent=2))

readme=f'''# CV6T-14C217-AR PSCM disassembly workspace\n\nTarget: Freescale/NXP MC56F8366 family, DSP56800E core.\n\nThis is a **conservative first-pass** disassembly. It never guesses an opcode: unknown instructions are emitted as `.word`. This makes the listing safe to annotate while the decoder is expanded.\n\n## Files\n- `CV6T-14C217-AR.conservative.asm` — complete occupied P-space listing.\n- `vectors.txt` — 86-vector table with decoded JSR targets.\n- `functions.csv` — call/ISR targets discovered from the validated JSR ABS19 encoding.\n- `peripheral_xrefs.csv` — verified absolute peripheral references (`F07C` and `8748` forms).\n- `vectors_startup.asm`, `pwma_reload_area.asm`, `flexcan_mb12_13_area.asm` — focused listings.\n- `P_space_le.bin` — P-space words, little-endian, gap-filled with FFFF.\n- `X_flash_{minx:04X}_{maxx:04X}_le.bin` — extracted X/Data Flash block.\n- `metadata.json` — extraction metadata.\n\n## Validated decoder rules currently implemented\n- `E254..E257 xxxx` -> `jsr P:<19-bit absolute>` (the high address bank is encoded by E254..E257).\n- `E708` -> `rts`.\n- `F07C xxxx` -> `move.w X:$xxxx,A`.\n- `8748 xxxx` -> `moveu.w #$xxxx,R0`.\n\n## Addressing\nVBF stores byte-pointer addresses. DSP56800E P/X memory is 16-bit word addressed, so VBF P addresses are divided by 2. The X block `0x04008C00..0x0400FFFF` maps to `X:$4600..$7FFF`.\n\n## Next decoder work\nThe highest-value additions are conditional branches, direct/indirect MOVE forms, bitfield operations (`bfclr/bfset/bftst*`), arithmetic, and RTI. Once those are added, recursive control-flow traversal can replace the current linear conservative listing.\n'''
(OUT/'README.md').write_text(readme)
print(json.dumps(meta,indent=2))
