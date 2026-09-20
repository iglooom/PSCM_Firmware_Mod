# FD22 snapshot VBF — independent verification

## Disposition

**READY FOR CONTROLLED TEST**

This disposition is conditional on every assertion in `verify_fd22_snapshot_vbf_independent.py` passing against the three files named below. It is a static artifact result, not evidence from a vehicle or bench. The verifier performed no CAN access and wrote no VBF or binary image.

## Independent method

The verifier does not import the builder, builder tests, an existing VBF parser, the builder's constants table, or the handler-word generator. It:

- parses each VBF from its raw bytes by matching the ASCII header braces and accepting only the unique block walk that consumes the file exactly;
- implements bitwise CRC-16/CCITT-FALSE and bitwise reflected CRC-32 locally for VBF block and file integrity;
- implements CRC-16/MCRF4XX and the little-endian 16-bit additive checksum as separate local algorithms for internal words A and B;
- forms the internal-B image only after proving that application block 0, the paired calibration block, and application block 1 are contiguous at `0x00000..0x80000`;
- compares stock and output payload bytes directly at their load addresses;
- reads and decodes the cave's 60 little-endian P words from the output VBF; and
- accepts only the small instruction grammar used by this handler, rejecting an unexpected word or instruction position.

## Inputs and container integrity

| File | SHA-256 | Blocks and stored/recomputed CRC-16/CCITT-FALSE | Stored/recomputed file CRC-32 |
|---|---|---|---|
| `CV6T-14C217-AR.VBF` | `cc68d97b1b8ca3ff9449d8e42ffa6ec16280f640f8b7928af4080b40592b96f5` | `0x00000000/0x9800: C15E`; `0x0001C000/0x64000: 1B6C`; `0x04008C00/0x7400: 9E5A` | `5BE7CF1E` |
| `CV6T-14C218-AX.VBF` | `6fdc0ad81727fd39db98a486f2ec2b4de928100138fc93b8c3e5c2a8e307c5f7` | `0x00009800/0x12800: A4AB` | `1F41A14D` |
| `CV6T-14C217-AR_FD22_SNAPSHOT.VBF` | `d4f8970c8c756fab6fe039455f0d0e5fb37156ba229f68410c5034b35dc8f370` | `0x00000000/0x9800: C15E`; `0x0001C000/0x64000: F18D`; `0x04008C00/0x7400: 9E5A` | `E0FECB04` |

All stored and recomputed values matched. All block walks ended exactly at EOF. Stock and output have the same three-block topology, frame offsets, and `data_start=0x492`. Blocks 0 and 2 and their CRC fields are byte-identical. Their normalized headers are byte-identical outside the fixed-width `file_checksum` value.

## Internal integrity words

Internal A is independently recomputed as CRC-16/MCRF4XX over application block 0 concatenated with application block 1 bytes `[0x1800:0x63FEA)`. Internal B is independently recomputed as the modulo-`2^16` sum of little-endian words in merged linear flash `[0x00000000:0x0007FFEC)`, including the paired calibration and repaired A.

| Image | A at flash `0x7FFEA` | Recomputed A | B at flash `0x7FFEC` | Recomputed B |
|---|---:|---:|---:|---:|
| Stock plus AX calibration | `D110` | `D110` | `3216` | `3216` |
| Snapshot plus AX calibration | `1ED4` | `1ED4` | `3507` | `3507` |

## Exact payload differences

There are exactly three intentional edited word-site spans and exactly 125 differing payload bytes:

| Edited site | Full site size | Before | After |
|---|---:|---|---|
| FD22 pointer, flash `0x1DD48..0x1DD4C` (`P:$0EEA4..$0EEA5`) | 4 bytes / 2 words | `8f050100` | `00380300` |
| Handler cave, flash `0x67000..0x67078` (`P:$33800..$3383B`) | 120 bytes / 60 words | 60 words of `E70A` | decoded body below |
| Internal A/B, flash `0x7FFEA..0x7FFEE` | 4 bytes / 2 words | `10d11632` | `d41e0735` |

Every one of the 62 pointer/cave words changed, and both checksum words changed. No payload byte outside these spans changed. Three octets inside changed word sites coincidentally retain their old value: `0x1DD4B`, `0x67052`, and `0x67077`. Consequently, the strict contiguous differing-byte runs are `0x1DD48..0x1DD4B`, `0x67000..0x67052`, `0x67053..0x67077`, and `0x7FFEA..0x7FFEE` (all ranges half-open). This explains why three edited sites contain four raw byte-difference runs and 125, rather than 128, differing bytes.

The full output FD22 record is:

```text
P:$0EEA2  FD22 0000 3800 0003 0000 0000
```

Combining its low and high pointer words gives exactly `P:$33800`, the first cave word.

## Cave words and semantic decode

The 60 words decoded directly from the output payload are:

```text
P:$33800  E080 D0B6 F07C 2DDE 8110 5C28 D0E6 0001
P:$33808  D1E6 0002 F07C 2DB9 8110 5C28 D0E6 0003
P:$33810  D1E6 0004 F07C 2D53 8110 5C28 D0E6 0005
P:$33818  D1E6 0006 F07C 2D52 8110 5C28 D0E6 0007
P:$33820  D1E6 0008 F07C 1CB1 8110 5C28 D0E6 0009
P:$33828  D1E6 000A F07C 171B 8110 5C28 D0E6 000B
P:$33830  D1E6 000C F07C 2D54 8110 5C28 D0E6 000D
P:$33838  D1E6 000E E58F E708
```

The decoded behavior is straight-line:

1. `E080` creates the zero format value in A and `D0B6` writes it as scratch byte offset 0 through the entry `R2` byte pointer.
2. Each `F07C address` loads one 16-bit X-memory source into A. `8110` retains A1 in B1, `5C28` shifts A right by eight, `D0E6 offset` writes the high byte, and `D1E6 offset` writes the retained low byte.
3. Source order is exactly `X:$2DDE`, `X:$2DB9`, `X:$2D53`, `X:$2D52`, `X:$1CB1`, `X:$171B`, `X:$2D54`.
4. Each source occurs in exactly one load group. Its high-byte store immediately precedes its low-byte store.
5. The stores are exactly 15 byte-pointer writes based on unchanged `R2`, at offsets `0,1,...,14`, each once.
6. `E58F` sets `Y0=15`; the sole terminal instruction is final `E708` (`RTS`).

The opcode meanings are tied to immutable OEM sequences at `P:$2075B`, `P:$20712`, `P:$2046C`, `P:$13B32..$13B40`, and `P:$1059D`; each of the seven `F07C source` pairs also occurs in the immutable stock image. The decoder consumes every cave word as one of these proved forms. Therefore there is no unknown instruction, branch, call, loop, indirect transfer, stack access, or stack adjustment. Its only memory writes are the 15 `R2` byte-pointer scratch stores. There is no absolute-X store or other OEM-state write. The decoded clobber set is only A, B, Y0, and condition codes; `R2`, C, and the unproved/nonvolatile registers are not written.

## Reproduction

```bash
cd /home/gl/Projects/ford/PSCM/Research
python3 work/lca_resume/verify_fd22_snapshot_vbf_independent.py
```

The command exits zero only after all assertions pass and ends with:

```text
READY FOR CONTROLLED TEST
```

Any failed assertion exits nonzero and prints:

```text
DO NOT FLASH
```
