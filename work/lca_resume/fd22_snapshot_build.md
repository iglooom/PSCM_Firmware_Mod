# FD22 snapshot VBF — initial build report

## Result

Built `CV6T-14C217-AR_FD22_SNAPSHOT.VBF` deterministically from the immutable OEM application `CV6T-14C217-AR.VBF`, paired with OEM calibration `CV6T-14C218-AX.VBF`.

- Output SHA-256: `d4f8970c8c756fab6fe039455f0d0e5fb37156ba229f68410c5034b35dc8f370`
- Output size: `479408` bytes
- VBF part metadata remains `CV6T-14C217-AR`, type `EXE`, version `2.3`
- VBF topology remains three blocks with `data_start 0x492` and zero trailing bytes
- No CAN or flash hardware was accessed

## Immutable input checks

The builder requires these exact whole-file OEM digests and refuses any drift:

| Input | Required and observed SHA-256 |
|---|---|
| `CV6T-14C217-AR.VBF` | `cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5` |
| `CV6T-14C218-AX.VBF` | `6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7` |

The three application payload blocks are additionally pinned to their OEM SHA-256 values. Every edit site is checked before mutation:

- `P:$0EEA4`: `058F 0001`
- `P:$33800..$3383B`: 60 words of OEM fill `E70A`
- block-1 internal word A: `D110`
- block-1 internal word B: `3216`

The output path is rejected if it resolves to either input path. Post-build SHA checks confirmed that the stock VBF, calibration VBF, and extracted OEM BIN files retained their expected hashes.

## Payload edits

All payload blocks were compared byte-for-byte. The only permitted word sites are the two callback-pointer words, the 60 handler cave words, and checksum words A/B. The builder requires all 62 functional words to change. Either checksum word may remain unchanged only when its independently recomputed value proves that unchanged value is mathematically correct.

| Site | Before | After |
|---|---|---|
| FD22 callback pointer `P:$0EEA4/$0EEA5` | `058F 0001` | `3800 0003` |
| Handler cave `P:$33800..$3383B` | 60 × `E70A` | verified 60-word stateless snapshot handler |
| Internal word A, flash `0x0007FFEA` | `D110` | `1ED4` |
| Internal word B, flash `0x0007FFEC` | `3216` | `3507` |

No cyclic hook, RAM allocation, state write, control branch, or other payload edit is present.

`vbftool diff` independently reported exactly three clusters:

```text
3 differing cluster(s), 125 bytes total
  0x01DD48..0x01DD4B  (3 B)  8f0501 -> 003803
  0x067000..0x067077  (119 B)  0ae70ae70ae70ae70ae70ae7 -> 80e0b6d07cf0de2d1081285c
  0x07FFEA..0x07FFEE  (4 B)  10d11632 -> d41e0735
```

The byte count is lower than 128 because three individual bytes happen to be identical at changed word sites. The builder audits by complete word sites and complete payload bytes, not by cluster byte count.

## Integrity repair and verification

Integrity was repaired in the required order:

1. Internal word A: CRC-16/MCRF4XX over OEM block 0 concatenated with modified block 1 `[START:0x63FEA)`, with firmware-derived `START = 0x1800`.
2. Internal word B: little-endian 16-bit sum over linear flash `[0x00000000:0x0007FFEC)`, including repaired word A and the paired calibration.
3. Touched VBF block CRC-16/CCITT-FALSE: `1B6C -> F18D`.
4. Header `file_checksum` CRC-32: `5BE7CF1E -> E0FECB04`.

The builder reparsed and read back the written artifact, independently revalidated words A/B, and asserted:

- exactly 2 callback-pointer words changed;
- exactly 60 cave words changed;
- checksum words A/B are mathematically valid;
- zero unexpected payload-byte changes;
- untouched payload blocks and their CRC fields are unchanged;
- block framing and offsets are unchanged;
- zero unexpected header-byte changes outside the fixed-width `file_checksum` digits.

The skill-provided verifier completed successfully:

```text
CV6T-14C217-AR_FD22_SNAPSHOT.VBF               OK  (3 blocks)
```

Its block summary was:

```text
blk0  load=0x00000000 len=0x00009800 crc16=0xC15E
blk1  load=0x0001C000 len=0x00064000 crc16=0xF18D
blk2  load=0x04008C00 len=0x00007400 crc16=0x9E5A
```

## Reproduction

From the repository root:

```bash
python3 -m unittest -v work/lca_resume/test_build_fd22_snapshot_vbf.py
python3 work/lca_resume/build_fd22_snapshot_vbf.py --selftest
python3 work/lca_resume/build_fd22_snapshot_vbf.py
T=~/.hermes/skills/software-development/vbf-firmware-container/scripts/vbftool.py
python3 "$T" verify CV6T-14C217-AR_FD22_SNAPSHOT.VBF
python3 "$T" diff CV6T-14C217-AR.VBF CV6T-14C217-AR_FD22_SNAPSHOT.VBF
```

Observed automated test result: 5 unit tests passed; all 12 integrated selftest checks passed.
